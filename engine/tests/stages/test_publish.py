import hashlib
import json
import math
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from lull import media
from lull.ledger import CallRecord, Ledger
from lull.media import loudness_profile
from lull.stages import mix as mixer
from lull.stages.finalize import OverlapError
from lull.stages.mix import duck_filter
from lull.stages.publish import (
    coverage,
    f113_flags,
    publish,
    to_extended_vtt,
    to_vtt,
    tts_disclosure,
    wcag_status,
)
from lull.stages.research import GlossaryEntry, ResearchResult
from lull.types import Claim, Cue, Gap, RunOptions, VisualEvent

SUPER = "nvidia/Nemotron-3.5-Super-VL"
LIGHT = "nvidia/Nemotron-3_5-Lightning"

# -- helpers ---------------------------------------------------------------------------------


def _cue(id_, start, dur, *, gap="g001", status="verified", text="A door opens.", ev=("e1",),
         audio="x.wav", claims=(), **kw) -> Cue:  # fmt: skip
    return Cue(
        id=id_, gap_id=gap, start=start, end=None if dur is None else start + dur, text=text,
        event_ids=list(ev), status=status, ahead=False, claims=list(claims),
        audio_path=None if dur is None else audio, audio_dur=dur, **kw,
    )  # fmt: skip


def _gap(id_, us, ue) -> Gap:
    return Gap(id=id_, start=us - 0.2, end=ue + 0.2, usable_start=us, usable_end=ue,
               capacity_words=int((ue - us) * 2.36))  # fmt: skip


def _ev(id_, t0, t1, kind="action", salience=4, text="Something happens.") -> VisualEvent:
    return VisualEvent(id=id_, t_start=t0, t_end=t1, kind=kind, text=text, verbatim_text=None,
                       salience=salience, said_in_dialogue=False)  # fmt: skip


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sine_wav(path: Path, seconds: float, freq: float, amp: float, rate: int = 22050) -> Path:
    n = round(seconds * rate)
    x = amp * np.sin(2 * np.pi * freq * np.arange(n) / rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return path


def _ledger(tmp_path: Path, run_id: str = "r1") -> Ledger:
    led = Ledger(tmp_path / "ledger.db", cap_run=0, cap_day=0, cap_total=0)
    for role, model, cost in [("watch", SUPER, 0.004), ("verify", SUPER, 0.002),
                              ("verify", SUPER, 0.002), ("repair", LIGHT, 0.0001)]:  # fmt: skip
        led.record(CallRecord(run_id=run_id, stage="s", role=role, provider="tokenfactory",
                              model=model, tokens_in=1000, tokens_cached=0, tokens_out=100,
                              latency_s=1.5, cost_usd=cost, ts=1.0))  # fmt: skip
    return led


# -- WebVTT ----------------------------------------------------------------------------------


def test_to_vtt_exact_two_cues():
    cues = [
        _cue("c002", 65.5, 1.25, gap="g002", text="Words  appear: R&D <beta> -->\nnow."),
        _cue("c001", 1.2, 2.9, text="A red door opens."),
    ]
    assert to_vtt(cues) == (
        "WEBVTT\nKind: descriptions\nLanguage: en\n"
        "\n"
        "c001\n00:00:01.200 --> 00:00:04.100\nA red door opens.\n"
        "\n"
        "c002\n00:01:05.500 --> 00:01:06.750\nWords appear: R&amp;D &lt;beta&gt; --&gt; now.\n"
    )


def test_to_vtt_only_voiced_standard_cues():
    cues = [
        _cue("c001", 1.2, 2.9),
        _cue("c002", 5.0, None, status="held", note="not verified"),
        _cue("c003", 8.0, 1.0, status="edited", text="Edited line."),
        _cue("x001", 9.0, 2.0, gap=None, status="extended"),
    ]
    out = to_vtt(cues, language="de")
    assert out.startswith("WEBVTT\nKind: descriptions\nLanguage: de\n\n")
    assert "c001" in out and "c003" in out and "Edited line." in out
    assert "c002" not in out and "x001" not in out


def test_to_vtt_without_cues_is_header_only():
    assert to_vtt([]) == "WEBVTT\nKind: descriptions\nLanguage: en\n"
    held = [_cue("c001", 1.0, None, status="held")]
    assert to_vtt(held) == "WEBVTT\nKind: descriptions\nLanguage: en\n"


def test_extended_vtt_notes_each_pause():
    cues = [
        _cue("x002", 30.0, 2.5, gap=None, status="extended", text="A chart appears."),
        _cue("x001", 12.0, 3.25, gap=None, status="extended", text="Words appear: Lull."),
        _cue("x003", 40.0, None, gap=None, status="held"),
    ]
    assert to_extended_vtt(cues) == (
        "WEBVTT\nKind: descriptions\nLanguage: en\n"
        "\n"
        "NOTE extended pause=3.250\n\nx001\n00:00:12.000 --> 00:00:15.250\nWords appear: Lull.\n"
        "\n"
        "NOTE extended pause=2.500\n\nx002\n00:00:30.000 --> 00:00:32.500\nA chart appears.\n"
    )


def test_voiced_cue_without_audio_is_an_error():
    with pytest.raises(ValueError, match="c001"):
        to_vtt([_cue("c001", 1.0, None, status="verified")])


# -- ducking ---------------------------------------------------------------------------------


def test_duck_filter_two_cues():
    cues = [_cue("c002", 5.0, 1.5, gap="g002"), _cue("c001", 1.0, 2.0)]
    assert duck_filter(cues) == (
        "volume=volume='1-0.683772*min(1,"
        "clip((t-0.800)/0.200,0,1)*clip((3.200-t)/0.200,0,1)"
        "+clip((t-4.800)/0.200,0,1)*clip((6.700-t)/0.200,0,1))'"
        ":eval=frame:enable='between(t,0.800,3.200)+between(t,4.800,6.700)'"
    )


def test_duck_filter_merges_windows_that_touch_and_handles_time_zero():
    # cues 0.3 s apart: their ramps would overlap, so the programme stays ducked between them
    cues = [_cue("c001", 0.1, 1.0), _cue("c002", 1.4, 1.0)]
    assert duck_filter(cues, duck_db=-6.0, ramp=0.25) == (
        "volume=volume='1-0.498813*min(1,"
        "clip((t+0.150)/0.250,0,1)*clip((2.650-t)/0.250,0,1))'"
        ":eval=frame:enable='between(t,-0.150,2.650)'"
    )


def test_held_cues_are_never_mixed_even_with_audio():
    held = _cue("c002", 5.0, 1.0, status="held")  # voiced once, then held
    ext = _cue("x001", 9.0, 1.0, gap=None, status="extended")
    std = [_cue("c003", 8.0, 1.0, gap="g002"), held, _cue("c001", 1.0, 1.0), ext]
    assert [c.id for c in mixer.voiced(std)] == ["c001", "c003"]
    assert [
        c.id for c in mixer.voiced_extended([held.model_copy(update={"gap_id": None}), ext])
    ] == ["x001"]


def test_extended_pauses_wait_for_a_standard_cue_and_merge_close_cues():
    std = [_cue("c001", 3.5, 1.0)]  # speaks 3.5-4.5
    ext = [
        _cue("x001", 0.02, 1.0, gap=None, status="extended"),  # at the very start -> 0
        _cue("x002", 4.0, 2.0, gap=None, status="extended"),  # inside c001 -> after it
        _cue("x003", 4.55, 1.0, gap=None, status="extended"),  # within 0.1 s of x002's pause
        _cue("x004", 30.0, 1.0, gap=None, status="extended"),  # past the end -> clamped
    ]
    groups = mixer._pauses(ext, 10.0, std)
    assert [(t, [c.id for c in g]) for t, g in groups] == [
        (0.0, ["x001"]), (4.5, ["x002", "x003"]), (9.9, ["x004"])
    ]  # fmt: skip
    segs = mixer._segments([t for t, _ in groups], [1.0, 3.0, 1.0])
    assert segs == [(0.0, 4.5, 1.0), (4.5, 9.9, 3.0), (9.9, None, 1.0)]


def test_duck_filter_without_cues_passes_audio_through():
    assert duck_filter([]) == "anull"
    with pytest.raises(ValueError):
        duck_filter([_cue("c001", 1.0, 1.0)], ramp=0.0)


# -- report logic ----------------------------------------------------------------------------


def test_f113_flags_crafted_case():
    gaps = [
        _gap("g001", 2.0, 5.0),  # empty, slide on screen, not described anywhere -> flag
        _gap("g002", 8.0, 9.5),  # empty but under 2 s
        _gap("g003", 12.0, 15.0),  # used by a voiced cue
        _gap("g004", 18.0, 21.0),  # empty, on-screen text of salience 3 only
        _gap("g005", 24.0, 27.0),  # empty, salient but an action (not text/scene/slide/chart)
        _gap("g006", 30.0, 33.0),  # empty, chart already described by c003
        _gap("g007", 36.0, 39.0),  # its only cue is held -> empty, scene change -> flag
    ]
    events = [
        _ev("e1", 1.0, 3.0, kind="slide", salience=5),
        _ev("e2", 8.5, 9.0, kind="on_screen_text", salience=5),
        _ev("e3", 12.5, 14.0, kind="on_screen_text", salience=5),
        _ev("e4", 18.5, 20.0, kind="on_screen_text", salience=3),
        _ev("e5", 24.5, 26.0, kind="action", salience=5),
        _ev("e6", 28.0, 31.0, kind="chart", salience=4),
        _ev("e7", 36.5, 37.0, kind="scene_change", salience=4),
        _ev("e8", 37.5, 38.0, kind="on_screen_text", salience=4),
    ]
    cues = [
        _cue("c001", 12.0, 2.0, gap="g003", ev=["e3"]),
        _cue("c003", 28.4, 1.0, gap="g000", ev=["e6"]),
        _cue("c004", 36.0, None, gap="g007", status="held", ev=["e7"]),
    ]
    flags = f113_flags(gaps, cues, events)
    assert [f["gap_id"] for f in flags] == ["g001", "g007"]
    assert flags[0]["event_ids"] == ["e1"]
    assert flags[0]["start"] == 2.0 and flags[0]["end"] == 5.0 and flags[0]["seconds"] == 3.0
    assert flags[1]["event_ids"] == ["e7", "e8"]


def test_coverage_counts_salient_events_in_voiced_cues():
    events = [
        _ev("e1", 0, 1, salience=5), _ev("e2", 1, 2, salience=4), _ev("e3", 2, 3, salience=4),
        _ev("e4", 3, 4, salience=3), _ev("e5", 4, 5, salience=5),
    ]  # fmt: skip
    cues = [
        _cue("c001", 0.0, 1.0, ev=["e1", "e4"]),
        _cue("c002", 2.0, None, status="held", ev=["e2"]),
    ]
    ext = [_cue("x001", 4.0, 1.0, gap=None, status="extended", ev=["e5"])]
    cov = coverage(cues, ext, events, extended_produced=True)
    assert cov == {
        "salient_events": 4,
        "described": 1,
        "ratio": 0.25,
        "described_with_extended": 2,
        "ratio_with_extended": 0.5,
        "undescribed": ["e2", "e3"],
    }
    assert coverage(cues, ext, events, extended_produced=False)["described_with_extended"] == 1
    assert coverage([], [], [_ev("e9", 0, 1, salience=2)], extended_produced=False)["ratio"] is None


def _wcag(**kw) -> dict[str, dict]:
    args = {"standard": 3, "extended_produced": False, "held": 0, "overlap_s": 0.0,
            "f113": 0} | kw  # fmt: skip
    return {row["criterion"]: row for row in wcag_status(**args)}


def test_wcag_all_met_when_every_check_passes():
    rows = _wcag(extended_produced=True)
    assert [rows[c]["status"] for c in ("1.2.3", "1.2.5", "1.2.7")] == ["met"] * 3
    assert "descriptions.vtt" in rows["1.2.3"]["via"] and "script.md" in rows["1.2.3"]["via"]
    assert "described.mp4" in rows["1.2.5"]["via"] and "G78" in rows["1.2.5"]["via"]
    assert "extended.mp4" in rows["1.2.7"]["via"] and "G8" in rows["1.2.7"]["via"]
    assert all(rows[c]["reasons"] == [] for c in rows)


def test_wcag_needs_review_on_overlap_f113_or_held():
    rows = _wcag(overlap_s=0.4, f113=1, held=2)
    assert rows["1.2.5"]["status"] == "needs review"
    assert rows["1.2.3"]["status"] == "needs review"
    reasons = " ".join(rows["1.2.5"]["reasons"])
    assert "0.400 s" in reasons and "F113" in reasons and "2 descriptions held" in reasons
    assert rows["1.2.7"]["status"] == "not claimed"
    assert rows["1.2.7"]["reasons"] == ["extended version not produced"]
    rows = _wcag(extended_produced=True, held=1)
    assert rows["1.2.7"]["status"] == "needs review"  # the held cue is missing from it too
    assert rows["1.2.7"]["reasons"] == ["1 description held for review"]
    assert "extended.mp4" in rows["1.2.7"]["via"]


def test_wcag_without_a_voiced_standard_track():
    rows = _wcag(standard=0)
    assert [rows[c]["status"] for c in ("1.2.3", "1.2.5", "1.2.7")] == [
        "needs review", "needs review", "not claimed"
    ]  # fmt: skip
    assert "no description is voiced" in rows["1.2.5"]["reasons"][0]
    # continuous speech: only the extended version carries descriptions (G8)
    rows = _wcag(standard=0, extended_produced=True)
    assert rows["1.2.5"]["status"] == "met" and "extended.mp4" in rows["1.2.5"]["via"]
    assert "described.mp4" not in rows["1.2.5"]["via"]


def test_tts_disclosure_says_reviewed_only_when_every_cue_was_edited():
    edited = [_cue("c001", 1.0, 1.0, status="edited")]
    assert tts_disclosure(edited) == (
        "Descriptions are voiced with synthetic speech (NVIDIA Magpie TTS); "
        "every line was reviewed before publishing."
    )
    mixed = edited + [_cue("c002", 3.0, 1.0, status="verified")]
    assert tts_disclosure(mixed).endswith("lines are awaiting review.")
    assert "NVIDIA Magpie TTS" in tts_disclosure(mixed)
    held_too = edited + [_cue("c003", 5.0, None, status="held")]
    assert tts_disclosure(held_too).endswith("lines are awaiting review.")
    assert tts_disclosure([]) == "No description was voiced in this run."
    assert tts_disclosure([_cue("c004", 5.0, None, status="held")]) == (
        "No description was voiced in this run."
    )


# -- publish without media (fakes) -----------------------------------------------------------


@pytest.fixture
def fake_media(monkeypatch):
    """mix, extended video and keyframes write placeholder files (no ffmpeg)."""
    calls: dict[str, list] = {"mix": [], "extended": [], "frames": []}

    def fake_mix(video, cues, out_dir, **kw):
        calls["mix"].append([c.id for c in cues])
        audio, vid = out_dir / mixer.AD_AUDIO, out_dir / mixer.AD_VIDEO
        audio.write_bytes(b"m4a")
        vid.write_bytes(b"mp4")
        return mixer.MixResult(ad_audio=audio, ad_video=vid)

    def fake_extended(video, cues_extended, out_dir, **kw):
        calls["extended"].append([c.id for c in cues_extended])
        return None

    def fake_frames(src, shots, **kw):
        calls["frames"].append([(round(t, 3), Path(d).name) for t, d in shots])
        for _, d in shots:
            Path(d).write_bytes(b"\xff\xd8jpg")
        return [Path(d) for _, d in shots]

    monkeypatch.setattr(media, "probe", lambda p: media.Probe(60.0, 640, 360, 25.0, True, "h264"))
    monkeypatch.setattr(mixer, "mix", fake_mix)
    monkeypatch.setattr(mixer, "extended_video", fake_extended)
    monkeypatch.setattr(media, "frames_at", fake_frames)
    return calls


def test_publish_refuses_on_overlap_and_writes_nothing(tmp_path, fake_media):
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"video")
    gaps = [_gap("g001", 2.0, 5.0), _gap("g002", 8.0, 10.0)]
    cues = [
        _cue("c001", 2.0, 2.5),
        _cue("c002", 8.0, 2.4, gap="g002"),  # runs 0.4 s past its pause into speech
    ]
    run_dir = tmp_path / "run"
    with pytest.raises(OverlapError) as err:
        publish(run_dir, video, cues, [], [_ev("e1", 2, 4)], gaps, None, _ledger(tmp_path), "r1",
                RunOptions())  # fmt: skip
    assert err.value.cue_ids == ["c002"]
    assert not run_dir.exists()
    assert fake_media["mix"] == [] and fake_media["frames"] == []


def test_publish_with_an_empty_standard_track(tmp_path, fake_media):
    """Continuous speech: no standard cue at all, publish still succeeds (Review Focus 2)."""
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"video bytes")
    events = [_ev("e1", 10.0, 14.0, kind="slide", salience=5, text="A slide titled Results.")]
    ext = [_cue("x001", 10.0, None, gap=None, status="held", ev=["e1"], note="not verified")]
    res = publish(tmp_path / "run", video, [], ext, events, [], None, _ledger(tmp_path), "r1",
                  RunOptions())  # fmt: skip
    run = tmp_path / "run"
    assert (run / "descriptions.vtt").read_text(encoding="utf-8") == (
        "WEBVTT\nKind: descriptions\nLanguage: en\n"
    )
    assert (run / "extended.vtt").read_text(encoding="utf-8") == (
        "WEBVTT\nKind: descriptions\nLanguage: en\n"
    )
    assert fake_media["mix"] == [[]]
    assert fake_media["frames"] == [[(12.0, "x001.jpg")]]
    assert "extended.mp4" not in res.files
    manifest = json.loads(res.manifest.read_text(encoding="utf-8"))
    rows = {r["criterion"]: r["status"] for r in manifest["summary"]["wcag"]}
    assert rows == {"1.2.3": "needs review", "1.2.5": "needs review", "1.2.7": "not claimed"}
    assert manifest["summary"]["cues"] == {"verified": 0, "edited": 0, "held": 1, "extended": 0}
    assert manifest["summary"]["held"] == [{"id": "x001", "reason": "not verified"}]
    html = (run / "report.html").read_text(encoding="utf-8")
    assert "extended version not produced" in html
    assert "research skipped" in html
    assert "No description was voiced in this run." in html


def test_publish_report_and_manifest_contents(tmp_path, fake_media):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"lecture video")
    gaps = [_gap("g001", 2.0, 5.0), _gap("g002", 20.0, 23.0)]
    events = [
        _ev("e1", 2.0, 4.0, kind="slide", salience=5, text="A slide."),
        _ev("e2", 20.5, 22.0, kind="on_screen_text", salience=4, text="A caption."),
    ]
    finding = {"rule": "R14-gender", "message": "gendered word 'man'", "span": [2, 5],
               "severity": "warning"}  # fmt: skip
    cues = [
        _cue("c001", 2.0, 2.5, text="A man & <a> slide.", ev=["e1"], findings=[finding],
             claims=[Claim(text="a slide", verdict="supported", at=2.5)],
             note="descriptor added for Proog"),
        _cue("c002", 20.0, None, gap="g002", status="held", ev=["e2"],
             note="not verified after 4 rounds: a caption (not visible)"),
    ]  # fmt: skip
    research = ResearchResult(
        glossary=[GlossaryEntry(term="proog", canonical="Proog", note="a character",
                                sources=["https://example.org/proog"])],
        skipped=False,
    )  # fmt: skip
    res = publish(tmp_path / "run", video, cues, [], events, gaps, research, _ledger(tmp_path),
                  "r1", RunOptions(voice="Sofia"))  # fmt: skip
    run = tmp_path / "run"
    names = set(res.files)
    assert {"descriptions.vtt", "extended.vtt", "cues.json", "script.md", "report.html",
            "described-audio.m4a", "described.mp4", "keyframes/c001.jpg",
            "keyframes/c002.jpg"} == names  # fmt: skip
    m = json.loads(res.manifest.read_text(encoding="utf-8"))
    assert res.manifest == run / "manifest.json"
    assert m["run_id"] == "r1"
    assert m["video"] == {"name": "lecture.mp4", "sha256": _sha(video), "duration": 60.0}
    assert set(m["outputs"]) == names
    for name, meta in m["outputs"].items():
        assert meta == {"sha256": _sha(run / name), "bytes": (run / name).stat().st_size}
    s = m["summary"]
    assert s["cues"] == {"verified": 1, "edited": 0, "held": 1, "extended": 0}
    assert s["coverage"]["described"] == 1 and s["coverage"]["salient_events"] == 2
    assert s["overlap_seconds"] == 0.0
    assert [f["gap_id"] for f in s["f113"]] == ["g002"]
    assert s["cost_usd"] == pytest.approx(0.0081)
    assert s["cost_per_min"] == pytest.approx(0.0081)
    by = {(g["role"], g["model"]): g for g in s["calls_by_role_model"]}
    assert by[("verify", SUPER)]["calls"] == 2
    assert m["versions"]["engine"] == "0.1.0"
    assert m["versions"]["models"] == sorted([SUPER, LIGHT])
    assert m["options"]["voice"] == "Sofia"

    html = (run / "report.html").read_text(encoding="utf-8")
    assert "A man &amp; &lt;a&gt; slide." in html and "<a> slide" not in html
    assert "R14-gender" in html and "gendered word" in html
    assert "not verified after 4 rounds" in html
    assert "https://example.org/proog" in html and "Proog" in html
    assert "0.000" in html  # overlap seconds
    assert "awaiting review" in html and "NVIDIA Magpie TTS" in html
    assert "needs review" in html and "not claimed" in html
    assert "http" not in html.replace("https://example.org/proog", "")  # self-contained

    script = (run / "script.md").read_text(encoding="utf-8")
    assert "00:00:02.000" in script and "verified" in script and "held" in script
    assert "a slide — supported" in script and "R14-gender" in script
    assert "descriptor added for Proog" in script
    assert "|" not in script  # no tables

    data = json.loads((run / "cues.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in data["cues"]] == ["c001", "c002"] and data["extended"] == []


# -- real ffmpeg -----------------------------------------------------------------------------

PROGRAMME_HZ = 440.0
CUE_HZ = 3000.0


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    """One real publish on a 6 s fixture: a verified 1 s cue at 2.0 s, a held cue and a 1 s
    extended cue at 4.0 s."""
    root = tmp_path_factory.mktemp("publish")
    video = root / "fixture.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=25:d=6",
         "-f", "lavfi", "-i", f"sine=frequency={PROGRAMME_HZ}:duration=6",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "6", str(video)],
        check=True, capture_output=True,
    )  # fmt: skip
    std_wav = _sine_wav(root / "c001.wav", 1.0, CUE_HZ, 0.25)
    ext_wav = _sine_wav(root / "x001.wav", 1.0, CUE_HZ, 0.25)
    gaps = [_gap("g001", 2.0, 3.5), _gap("g002", 4.6, 5.8)]
    events = [
        _ev("e1", 1.5, 3.5, kind="slide", salience=5),
        _ev("e2", 4.6, 5.5, kind="action", salience=4),
        _ev("e3", 3.8, 4.4, kind="on_screen_text", salience=5),
    ]
    cues = [
        _cue("c001", 2.0, 1.0, ev=["e1"], audio=str(std_wav)),
        _cue("c002", 4.6, None, gap="g002", status="held", ev=["e2"], note="not verified"),
    ]
    ext = [_cue("x001", 4.0, 1.0, gap=None, status="extended", ev=["e3"], audio=str(ext_wav))]
    run = root / "run"
    res = publish(run, video, cues, ext, events, gaps, None, _ledger(root), "r1", RunOptions())
    return {"video": video, "run": run, "res": res, "root": root}


def _streams(path: Path) -> tuple[list[dict], float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name:stream_tags:stream_disposition=default:format=duration",
         "-of", "json", str(path)],
        check=True, capture_output=True,
    )  # fmt: skip
    info = json.loads(out.stdout)
    return info["streams"], float(info["format"]["duration"])


def _mean_db(profile, windows) -> float:
    vals = [db for t, db in profile if any(a <= t and t + 0.1 <= b for a, b in windows)]
    power = sum(10 ** (v / 10) for v in vals) / len(vals)
    return 10 * math.log10(power)


def _bands(src: Path, root: Path) -> tuple[Path, Path, dict]:
    """The programme band (<1 kHz), the cue band (>2 kHz) as 16-bit WAVs, and the loudness of
    the whole signal, from one ffmpeg run."""
    lo, hi = root / f"{src.stem}.lo.wav", root / f"{src.stem}.hi.wav"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-nostats", "-i", str(src), "-filter_complex",
         "[0:a]asplit=3[a][b][c];[a]lowpass=f=1000,lowpass=f=1000[lo];"
         "[b]highpass=f=2000,highpass=f=2000[hi];[c]ebur128=peak=true:framelog=verbose[r]",
         "-map", "[lo]", "-ac", "1", "-c:a", "pcm_s16le", str(lo),
         "-map", "[hi]", "-ac", "1", "-c:a", "pcm_s16le", str(hi),
         "-map", "[r]", "-f", "null", "-"],
        check=True, capture_output=True,
    )  # fmt: skip
    return lo, hi, mixer._loudness_stats(proc.stderr.decode("utf-8", "replace"))


def test_mix_ducks_the_programme_under_the_cue(published):
    lo, hi, loud = _bands(published["run"] / "described-audio.m4a", published["root"])
    assert loud["input_i"] == pytest.approx(-24.0, abs=0.5)  # loudnorm I=-24
    assert loud["input_tp"] <= -2.0
    with wave.open(str(lo), "rb") as w:
        assert w.getframerate() == 48000
        assert w.getnframes() / w.getframerate() == pytest.approx(6.0, abs=0.1)
    inside, outside = [(2.1, 2.9)], [(0.3, 1.5), (3.5, 5.7)]
    programme = loudness_profile(lo)
    drop = _mean_db(programme, outside) - _mean_db(programme, inside)
    print(f"programme {drop:.2f} dB quieter under the cue")
    assert 8.0 <= drop <= 12.0, f"programme {drop:.1f} dB quieter under the cue"
    voice = loudness_profile(hi)
    assert _mean_db(voice, inside) - _mean_db(voice, outside) >= 30.0  # the cue is placed


def test_described_mp4_has_described_and_original_tracks(published):
    streams, duration = _streams(published["run"] / "described.mp4")
    assert duration == pytest.approx(6.0, abs=0.15)
    video = [s for s in streams if s["codec_type"] == "video"]
    audio = [s for s in streams if s["codec_type"] == "audio"]
    assert len(video) == 1 and video[0]["codec_name"] == "h264"  # copied
    assert [s["tags"]["handler_name"] for s in audio] == ["Described", "Original"]
    assert [s["tags"]["language"] for s in audio] == ["eng", "eng"]
    assert [s["disposition"]["default"] for s in audio] == [1, 0]


def test_extended_mp4_freezes_for_the_extended_cue(published):
    res = published["res"]
    assert "extended.mp4" in res.files
    streams, duration = _streams(res.files["extended.mp4"])
    assert duration == pytest.approx(7.0, abs=0.15)
    assert [s["codec_type"] for s in streams] == ["video", "audio"]
    assert streams[1]["tags"]["handler_name"] == "Described (extended)"
    # programme 0-4 s, a 1 s pause for x001 at 4.0, then the programme's 4-6 s at 5-7 s
    lo, hi, loud = _bands(res.files["extended.mp4"], published["root"])
    assert loud["input_i"] == pytest.approx(-24.0, abs=0.5)
    programme, voice = loudness_profile(lo), loudness_profile(hi)
    pause, before, after, std = [(4.1, 4.9)], [(0.3, 1.5)], [(5.2, 6.8)], [(2.1, 2.9)]
    assert _mean_db(programme, after) - _mean_db(programme, pause) >= 30.0  # silent pause
    assert _mean_db(programme, before) - _mean_db(programme, std) >= 8.0  # still ducked
    assert _mean_db(voice, pause) - _mean_db(voice, after) >= 30.0  # x001 in the pause
    assert _mean_db(voice, std) - _mean_db(voice, before) >= 30.0  # c001 where it was


def test_published_files_match_manifest(published):
    run, res = published["run"], published["res"]
    m = json.loads(res.manifest.read_text(encoding="utf-8"))
    assert set(m["outputs"]) == set(res.files)
    for name in ("described-audio.m4a", "described.mp4", "extended.mp4", "keyframes/c001.jpg",
                 "keyframes/c002.jpg", "keyframes/x001.jpg", "descriptions.vtt"):  # fmt: skip
        path = run / name
        assert m["outputs"][name] == {"sha256": _sha(path), "bytes": path.stat().st_size}
    for name in ("keyframes/c001.jpg", "keyframes/x001.jpg"):
        assert (run / name).read_bytes()[:2] == b"\xff\xd8"
    rows = {r["criterion"]: r["status"] for r in m["summary"]["wcag"]}
    assert rows == {"1.2.3": "needs review", "1.2.5": "needs review", "1.2.7": "needs review"}
    assert m["summary"]["held"] == [{"id": "c002", "reason": "not verified"}]
    assert m["video"]["duration"] == pytest.approx(6.0, abs=0.1)
    vtt = (run / "descriptions.vtt").read_text(encoding="utf-8")
    assert "c001\n00:00:02.000 --> 00:00:03.000\n" in vtt
    assert "NOTE extended pause=1.000" in (run / "extended.vtt").read_text(encoding="utf-8")
