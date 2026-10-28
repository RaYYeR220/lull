"""`lull export-showcase`: a finished run folder -> the files and showcase.json for the site."""

from __future__ import annotations

import json
import re
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from locks import assert_locked
from lull import cli, media
from lull.showcase import SHOWCASE, ShowcaseError, export_showcase

DURATION = 4.0


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True, capture_output=True)


def _wav(path: Path, seconds: float, freq: float = 1000.0, rate: int = 22050) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = round(seconds * rate)
    x = 0.3 * np.sin(2 * np.pi * freq * np.arange(n) / rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return path


def _entry(key: str, data: dict) -> str:
    return json.dumps({"key": key, "data": data})


def _cue(id_, start, dur, text, *, gap="g001", status="verified", audio=None, claims=(), **kw):
    return {"id": id_, "gap_id": gap, "start": start, "end": None if dur is None else start + dur,
            "text": text, "event_ids": ["e001"], "status": status, "ahead": False,
            "claims": list(claims), "audio_path": audio, "audio_dur": dur, "tempo": 1.0,
            "findings": [], "note": None, **kw}  # fmt: skip


def _claim(text, verdict="supported", at=1.0):
    return {"text": text, "verdict": verdict, "at": at}


SUMMARY = {
    "cues": {"verified": 2, "edited": 0, "approved": 0, "held": 1, "extended": 1},
    "coverage": {"important_events": 4, "salient_events": 4, "described": 2, "ratio": 0.5,
                 "described_with_extended": 3, "ratio_with_extended": 0.75,
                 "undescribed": ["e009"], "repetitive": 1, "watch_salient": 5},
    "repetitive": [{"id": "e007", "text": "The same logo again."}],
    "overlap_seconds": 0.0,
    "f113": [{"gap_id": "g002", "start": 2.2, "end": 4.4, "seconds": 2.2,
              "event_ids": ["e009"], "events": [{"id": "e009", "kind": "slide", "text": "x"}]}],
    "cost_usd": 0.0123,
    "spend": {"model_usd_this_run": 0.0123, "tavily_searches_this_run": 2,
              "tavily_notional_usd_this_run": 0.016, "model_usd_all_runs": 0.05},
    "wcag": [{"criterion": "1.2.5", "level": "AA", "name": "Audio Description (Prerecorded)",
              "status": "needs review", "via": "described.mp4",
              "reasons": ["1 description held for review"]}],
}  # fmt: skip


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory) -> Path:
    """A finished run, as publish leaves it: 4 s 1280x720 with a 440 Hz programme 0-2 s then
    silence; c001 verified (its first draft had a claim the frames did not show), c002 held,
    x003 extended (frozen at 1.0 s), c004 verified with a look-ahead."""
    root = tmp_path_factory.mktemp("showcase") / "talk-1234abcd"
    root.mkdir()
    _ffmpeg(
        "-f", "lavfi", "-i", f"testsrc2=s=1280x720:r=25:d={DURATION}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration=2,apad=whole_dur={DURATION}",
        "-f", "lavfi", "-i", f"sine=frequency=2000:duration={DURATION}",
        "-map", "0:v", "-map", "2:a", "-map", "1:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac:a:1", "6",
        "-t", str(DURATION),
        "-metadata:s:a:0", "handler_name=Described", "-metadata:s:a:1", "handler_name=Original",
        str(root / "described.mp4"),
    )  # fmt: skip
    _ffmpeg("-i", str(root / "described.mp4"), "-map", "0:a:0", "-c:a", "copy",
            str(root / "described-audio.m4a"))  # fmt: skip
    (root / "descriptions.vtt").write_text("WEBVTT\n\nc001\n00:00:00.500 --> 00:00:01.500\n"
                                           "A red door opens.\n", encoding="utf-8")  # fmt: skip
    (root / "extended.vtt").write_text("WEBVTT\n", encoding="utf-8")
    (root / "keyframes").mkdir()
    for k in ("c001", "c002", "x003", "c004"):
        (root / "keyframes" / f"{k}.jpg").write_bytes(b"\xff\xd8jpg")
    audio = {}
    for k, dur in (("c001", 1.0), ("x003", 0.6), ("c004", 0.8)):
        rel = f"work/finalize/{k}-0123456789ab/audio/{k}.wav"
        _wav(root / rel, dur)
        audio[k] = rel
    first = [_claim("A red door opens."), _claim("A cat sits on the step.", "not_visible", None)]
    cues = {
        "run_id": "talk-1234abcd",
        "language": "en",
        "cues": [
            _cue("c001", 0.5, 1.0, "A red door opens.", audio=audio["c001"],
                 claims=[_claim("A red door opens.")]),
            _cue("c002", 2.2, None, "A dog barks.", gap="g002", status="held"),
            _cue("c004", 3.0, 0.8, "In a moment, a sign appears.", gap="g003",
                 audio=audio["c004"], claims=[_claim("A sign appears.", at=3.5)], ahead=True),
        ],
        "extended": [
            _cue("x003", 1.4, 0.6, "Words appear: Lull.", gap=None, status="extended",
                 audio=audio["x003"], claims=[_claim("Words appear: Lull.")], freeze_at=1.0),
        ],
        "gaps": [
            {"id": "g001", "start": 0.3, "end": 1.7, "usable_start": 0.5, "usable_end": 1.5,
             "capacity_words": 3, "protected": []},
            {"id": "g002", "start": 2.0, "end": 2.9, "usable_start": 2.2, "usable_end": 2.7,
             "capacity_words": 1, "protected": []},
            {"id": "g003", "start": 2.8, "end": 4.0, "usable_start": 3.0, "usable_end": 3.8,
             "capacity_words": 2, "protected": []},
        ],
        "speech": [{"start": 0.0, "end": 0.3}, {"start": 1.7, "end": 2.0}],
    }  # fmt: skip
    (root / "cues.json").write_text(json.dumps(cues), encoding="utf-8")
    manifest = {"run_id": "talk-1234abcd", "video": {"name": "talk.mp4", "duration": DURATION},
                "summary": SUMMARY, "versions": {"models": ["vl-model", "text-model"]}}  # fmt: skip
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stages = root / "stages"
    (stages / "finalize").mkdir(parents=True)
    words = [{"text": "Hello.", "start": 0.0, "end": 0.3, "speaker": 1},
             {"text": "Bye.", "start": 1.7, "end": 2.0, "speaker": 1}]  # fmt: skip
    (stages / "listen.json").write_text(_entry("k", {"words": words}), encoding="utf-8")
    finished = {
        "c001": {**cues["cues"][0], "checks": [
            {"text": "A red door opens. A cat sits on the step.", "claims": first},
            {"text": "A red door opens.", "claims": [_claim("A red door opens.")]},
        ]},
        "c004": {**cues["cues"][2], "checks": [
            {"text": "In a moment, a sign appears.", "claims": [_claim("A sign appears.")]},
        ]},
    }  # fmt: skip
    for k, data in finished.items():
        (stages / "finalize" / f"{k}.json").write_text(_entry("k", data), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def exported(run_dir, tmp_path_factory) -> tuple[Path, dict]:
    out = tmp_path_factory.mktemp("site") / "ed90"
    path = export_showcase(run_dir, out, credit="Blender Foundation (CC BY)")
    assert path == out / SHOWCASE
    return out, json.loads(path.read_text(encoding="utf-8"))


def test_showcase_json_has_exactly_the_site_schema(exported):
    _, data = exported
    assert list(data) == [
        "id", "title", "credit", "duration", "loudness", "speech", "gaps", "cues", "words",
        "audio", "cueAudioBase", "verifyExample", "costUsd", "summary",
    ]  # fmt: skip
    assert (data["id"], data["title"], data["credit"]) == (
        "talk-1234abcd", "talk", "Blender Foundation (CC BY)"
    )  # fmt: skip
    assert data["duration"] == DURATION and data["costUsd"] == 0.0123
    assert data["speech"] == [[0.0, 0.3], [1.7, 2.0]]
    assert data["gaps"] == [
        {"id": "g001", "start": 0.5, "end": 1.5, "capacity": 3},
        {"id": "g002", "start": 2.2, "end": 2.7, "capacity": 1},
        {"id": "g003", "start": 3.0, "end": 3.8, "capacity": 2},
    ]
    assert data["words"] == [{"t": "Hello.", "s": 0.0, "e": 0.3}, {"t": "Bye.", "s": 1.7, "e": 2.0}]
    assert data["audio"] == {"original": "video.mp4", "described": "described-audio.m4a"}
    assert data["cueAudioBase"] == "audio/"


def test_cues_are_the_published_lines_ending_with_their_audio(exported):
    _, data = exported
    assert data["cues"] == [
        {
            "id": "c001",
            "start": 0.5,
            "end": 1.5,
            "text": "A red door opens.",
            "status": "verified",
            "ahead": False,
            "extended": False,
        },
        {
            "id": "x003",
            "start": 1.0,
            "end": 1.6,
            "text": "Words appear: Lull.",
            "status": "extended",
            "ahead": False,
            "extended": True,
        },
        {
            "id": "c004",
            "start": 3.0,
            "end": 3.8,
            "text": "In a moment, a sign appears.",
            "status": "verified",
            "ahead": True,
            "extended": False,
        },
    ]  # fmt: skip  (held c002 is not published; x003 sits at its freeze point)


def test_the_run_summary_comes_with_the_showcase(exported):
    """What the report says about the run, for the page: coverage of the important events
    (with the extended version too), what the plan judged repetitive, F113 pauses, overlap,
    the counts, the WCAG statuses with what blocks them, the models and the spend."""
    _, data = exported
    assert data["summary"] == {
        "counts": {"verified": 2, "edited": 0, "approved": 0, "held": 1, "extended": 1},
        "coverage": {"importantEvents": 4, "described": 2, "ratio": 0.5,
                     "describedWithExtended": 3, "ratioWithExtended": 0.75,
                     "undescribed": ["e009"]},
        "repetitive": [{"id": "e007", "text": "The same logo again."}],
        "f113": [{"gapId": "g002", "start": 2.2, "end": 4.4, "eventIds": ["e009"]}],
        "overlapSeconds": 0.0,
        "wcag": [{"criterion": "1.2.5", "level": "AA", "name": "Audio Description (Prerecorded)",
                  "status": "needs review", "blockers": ["1 description held for review"]}],
        "models": ["vl-model", "text-model"],
        "spend": {"modelUsdThisRun": 0.0123, "webSearchesThisRun": 2,
                  "webSearchListUsdThisRun": 0.016, "modelUsdAllRunsOfRunId": 0.05},
    }  # fmt: skip


def test_the_verify_example_keeps_the_first_draft_and_its_verdicts(exported):
    _, data = exported
    assert data["verifyExample"] == {
        "cueId": "c001",
        "cue": "A red door opens. A cat sits on the step.",
        "claims": [
            {"text": "A red door opens.", "verdict": "supported"},
            {"text": "A cat sits on the step.", "verdict": "not_visible"},
        ],
    }


def test_loudness_is_the_original_audio_per_100_ms(exported):
    _, data = exported
    loud = data["loudness"]
    assert len(loud) == pytest.approx(DURATION / 0.1, abs=1)
    assert all(isinstance(v, float) and v >= -120.0 and round(v, 1) == v for v in loud)
    assert min(loud[2:18]) > -30  # the 440 Hz programme (not the 2 kHz description track)
    assert max(loud[22:38]) < -60  # then silence


def test_media_is_small_and_relative(exported, run_dir):
    out, data = exported
    names = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert names == [
        "audio/c001.m4a", "audio/c004.m4a", "audio/x003.m4a", "described-audio.m4a",
        "descriptions.vtt", "extended.vtt", "keyframes/c001.jpg", "keyframes/c002.jpg",
        "keyframes/c004.jpg", "keyframes/x003.jpg", "showcase.json", "video.mp4",
    ]  # fmt: skip
    video = media.probe(out / "video.mp4")
    assert video.height == 480 and video.width % 2 == 0 and video.video_codec == "h264"
    assert video.audio_codec == "aac" and video.audio_streams == 1
    channels = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels",
         "-of", "csv=p=0", str(out / "video.mp4")], capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    assert channels == "2"  # a 5.1 original is folded down for the browser
    body = (out / "video.mp4").read_bytes()
    assert 0 < body.find(b"moov") < body.find(b"mdat")  # faststart: the index comes first
    for k, dur in (("c001", 1.0), ("x003", 0.6), ("c004", 0.8)):
        info = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_name,channels,bit_rate:format=duration", "-of", "json",
             str(out / "audio" / f"{k}.m4a")], capture_output=True, check=True,
        ).stdout)  # fmt: skip
        (stream,) = info["streams"]
        assert stream["codec_name"] == "aac" and stream["channels"] == 1
        assert int(stream["bit_rate"]) <= 72_000
        assert float(info["format"]["duration"]) == pytest.approx(dur, abs=0.06)
    assert (out / "described-audio.m4a").read_bytes() == (
        run_dir / "described-audio.m4a"
    ).read_bytes()
    text = (out / SHOWCASE).read_text(encoding="utf-8")
    for value in _strings(json.loads(text)):
        assert not re.match(r"^([A-Za-z]:[\\/]|/|\\\\)", value), value
    assert str(run_dir) not in text and run_dir.as_posix() not in text


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def test_without_a_draft_that_lost_a_claim_the_first_verified_cue_is_the_example(run_dir, tmp_path):
    import shutil

    copy = tmp_path / "run"
    shutil.copytree(run_dir, copy)
    (copy / "stages" / "finalize" / "c001.json").unlink()  # no history for c001
    data = json.loads(export_showcase(copy, tmp_path / "out", id="demo", title="Demo")
                      .read_text(encoding="utf-8"))  # fmt: skip
    assert (data["id"], data["title"], data["credit"]) == ("demo", "Demo", "")
    assert data["verifyExample"] == {
        "cueId": "c001",
        "cue": "A red door opens.",
        "claims": [{"text": "A red door opens.", "verdict": "supported"}],
    }


def test_a_folder_that_is_not_a_finished_run_is_refused(tmp_path):
    (tmp_path / "run").mkdir()
    with pytest.raises(ShowcaseError, match="manifest.json"):
        export_showcase(tmp_path / "run", tmp_path / "out")


def test_export_showcase_command(run_dir, tmp_path):
    out = tmp_path / "site"
    args = ["export-showcase", str(run_dir), str(out), "--id", "ed90",
            "--title", "Elephants Dream", "--credit", "CC BY"]  # fmt: skip
    r = CliRunner().invoke(cli.app, args)
    assert r.exit_code == 0, r.output
    data = json.loads((out / SHOWCASE).read_text(encoding="utf-8"))
    assert (data["id"], data["title"], data["credit"]) == ("ed90", "Elephants Dream", "CC BY")
    assert "3 cues" in r.output and "showcase.json" in r.output
    r = CliRunner().invoke(cli.app, ["export-showcase", str(tmp_path / "nope"), str(out)])
    assert r.exit_code == 1 and "manifest.json" in r.output


# -- reuse and local files ------------------------------------------------------------------------


def test_a_reused_folder_keeps_no_old_cue_audio_or_keyframes(run_dir, tmp_path):
    """The cue audio and keyframes of an earlier export do not linger."""
    out = tmp_path / "site"
    for name in ("audio/c099.m4a", "keyframes/c099.jpg"):
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(b"old")
    export_showcase(run_dir, out)
    assert sorted(p.name for p in (out / "audio").iterdir()) == ["c001.m4a", "c004.m4a",
                                                                   "x003.m4a"]  # fmt: skip
    assert "c099.jpg" not in {p.name for p in (out / "keyframes").iterdir()}


def test_every_showcase_input_is_locked_to_local_files(run_dir, tmp_path, monkeypatch):
    """The export's probe and ffmpeg calls read local files only, like the pipeline's."""
    seen: list[list[str]] = []
    real = media.subprocess.run

    def spy(cmd, *args, **kwargs):
        seen.append([str(c) for c in cmd])
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(media.subprocess, "run", spy)
    export_showcase(run_dir, tmp_path / "site")
    assert len(seen) >= 5  # probe, video, sound, 3 cue files
    for cmd in seen:
        assert_locked(cmd)


def test_the_command_says_what_the_cost_is():
    """costUsd is the model spend of the run that published; summary.spend holds the rest."""
    r = CliRunner().invoke(cli.app, ["export-showcase", "--help"], terminal_width=200)
    text = " ".join(r.output.split())
    assert r.exit_code == 0 and "costUsd" in text and "model spend of the attempt" in text


# -- where the export may write -------------------------------------------------------------------


def test_exporting_into_the_run_folder_itself_is_refused_before_anything_goes(run_dir):
    before = sorted(p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*"))
    for out in (run_dir, run_dir / ".", run_dir / "keyframes" / ".."):
        with pytest.raises(ShowcaseError, match="run folder"):
            export_showcase(run_dir, out)
    assert sorted(p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*")) == before


def test_exporting_inside_the_run_folder_or_around_it_is_refused(run_dir):
    before = sorted(p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*"))
    for out in (run_dir / "site", run_dir / "keyframes" / "x", run_dir.parent):
        with pytest.raises(ShowcaseError, match="run folder"):
            export_showcase(run_dir, out)
    assert sorted(p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*")) == before
    assert not (run_dir / "site").exists()
