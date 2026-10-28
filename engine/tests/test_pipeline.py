import copy
import inspect
import json
import re
import shutil
import threading
import time
from pathlib import Path

import httpx
import pytest

import lull
from lull import pipeline
from lull.config import get_settings
from lull.ledger import BudgetExceeded, CallRecord, Ledger
from lull.media import MediaError
from lull.pipeline import (
    Event,
    budget_estimate,
    cue_context,
    introduced_before,
    run,
    run_id_for,
    source_digest,
)
from lull.showcase import export_showcase
from lull.stages.finalize import OverlapError
from lull.store import file_sha256
from lull.tavily import TavilyError
from lull.tf import TokenFactory
from lull.types import Cue, RunOptions, Span, VisualEvent, Word
from pipeline_fakes import (
    CUE_TEXT,
    PLAN,
    TERMS,
    WATCH,
    FakeSpeech,
    FakeTF,
    description,
    verify_all,
)

STAGES = ["listen", "watch", "research", "plan", "schedule", "finalize", "publish"]
CACHED = STAGES[:-1]  # publish always writes the outputs again
OUTPUTS = {
    "descriptions.vtt",
    "extended.vtt",
    "described-audio.m4a",
    "described.mp4",
    "extended.mp4",
    "cues.json",
    "script.md",
    "report.html",
}


def _ledger(root: Path, **caps) -> Ledger:
    return Ledger(
        root / "ledger.db", **{"cap_run": None, "cap_day": None, "cap_total": None, **caps}
    )


def _run(video, run_dir, ledger, *, tf=None, speech=None, opts=None, wps=3.0, run_id="t1"):
    tf = tf or FakeTF(ledger)
    speech = speech or FakeSpeech()
    events: list[Event] = []
    res = run(
        video,
        run_dir,
        opts or RunOptions(),
        tf=tf,
        speech=speech,
        tavily=None,
        ledger=ledger,
        on_event=events.append,
        run_id=run_id,
        wps=wps,
    )
    return res, tf, speech, events


def _ends(events: list[Event]) -> dict[str, dict]:
    return {e.stage: e.data for e in events if e.type == "stage_end"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """LULL_HOME in tmp_path: the real ledger and voices.json are never touched."""
    monkeypatch.setenv("LULL_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    yield tmp_path / "home"
    get_settings.cache_clear()


# -- a whole run with real media, then the same run again ---------------------------------------


@pytest.fixture(scope="module")
def runs(tone_slides, tmp_path_factory):
    root = tmp_path_factory.mktemp("pipeline")
    ledger = _ledger(root)
    run_dir = root / "run"
    first = _run(tone_slides, run_dir, ledger)
    second = _run(tone_slides, run_dir, ledger)
    return {"run_dir": run_dir, "ledger": ledger, "first": first, "second": second}


def test_full_run_publishes_every_output(runs):
    res, tf, speech, _ = runs["first"]
    run_dir = runs["run_dir"]
    assert OUTPUTS <= set(res.files)
    for name in OUTPUTS:
        assert (run_dir / name).stat().st_size > 0, name
    manifest = json.loads(res.manifest.read_text(encoding="utf-8"))
    assert manifest["run_id"] == "t1"
    summary = manifest["summary"]
    assert summary["cues"] == {"verified": 2, "edited": 0, "approved": 0, "held": 0, "extended": 1}
    assert summary["overlap_seconds"] == 0.0
    assert summary["research"] == {"skipped": True, "terms": 1, "errors": []}
    cues = json.loads((run_dir / "cues.json").read_text(encoding="utf-8"))
    assert [(c["id"], c["gap_id"], c["status"], c["text"]) for c in cues["cues"]] == [
        ("c001", "g001", "verified", CUE_TEXT["c001"]),
        ("c002", "g002", "verified", CUE_TEXT["c002"]),
    ]
    assert [(c["id"], c["status"]) for c in cues["extended"]] == [("x002", "extended")]
    # inside the run directory, in a folder named after the cue's cache key
    audio = cues["cues"][0]["audio_path"]
    assert re.fullmatch(r"work/finalize/c001-[0-9a-f]{12}/audio/c001\.wav", audio), audio
    vtt = (run_dir / "descriptions.vtt").read_text(encoding="utf-8")
    assert f"c001\n00:00:01.800 --> 00:00:03.800\n{CUE_TEXT['c001']}\n" in vtt
    assert "<html" in (run_dir / "report.html").read_text(encoding="utf-8").lower()
    assert tf.roles() == ["watch", "research", "plan", "verify", "verify", "verify"]
    assert speech.calls == ["transcribe", "diarize"] + ["synthesize"] * 3
    stages = run_dir / "stages"
    names = sorted(p.relative_to(stages).as_posix() for p in stages.rglob("*.json"))
    assert [n for n in names if not n.startswith("research/")] == [
        "finalize/c001.json",
        "finalize/c002.json",
        "finalize/x002.json",
        "listen.json",
        "plan.json",
        "research.json",
        "schedule.json",
        "watch.json",
    ]
    assert [re.sub(r"-[0-9a-f]{16}", "-<key>", n) for n in names if n.startswith("research/")] == [
        "research/terms-<key>.json"  # research's own steps (no Tavily: no lookups)
    ]


def test_a_published_run_exports_for_the_showcase(runs, tmp_path):
    """The exporter reads what a real run publishes (cues.json timeline, finalize history)."""
    path = export_showcase(runs["run_dir"], tmp_path / "site", id="tone")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [(c["id"], c["status"]) for c in data["cues"]] == [
        ("x002", "extended"), ("c001", "verified"), ("c002", "verified"),
    ]  # fmt: skip
    assert data["cues"][1]["start"] == 1.8 and data["gaps"][0] == {
        "id": "g001", "start": 1.8, "end": 6.25, "capacity": 13,
    }  # fmt: skip
    assert data["speech"] == [[0.3, 1.6], [11.2, 11.95]]
    assert [w["t"] for w in data["words"]] == ["Hi,", "I", "am", "Ada.", "Bye."]
    assert data["verifyExample"]["cueId"] == "c001"  # every claim held: the first verified cue
    assert data["verifyExample"]["cue"] == CUE_TEXT["c001"]
    assert len(data["loudness"]) == 120 and max(data["loudness"][:35]) > -30
    for name in ("video.mp4", "described-audio.m4a", "audio/c001.m4a", "audio/x002.m4a"):
        assert (tmp_path / "site" / name).stat().st_size > 0, name


def test_run_reports_stages_calls_cues_warnings_and_done(runs):
    _, tf, _, events = runs["first"]
    assert all(isinstance(e, Event) and e.ts > 0 for e in events)
    marks = [(e.type, e.stage) for e in events if e.type in ("stage_start", "stage_end")]
    assert marks == [(t, s) for s in STAGES for t in ("stage_start", "stage_end")]
    ends = _ends(events)
    assert all(d["cached"] is False and d["seconds"] >= 0 for d in ends.values())
    assert (ends["listen"]["gaps"], ends["listen"]["windows"], ends["listen"]["clamped"]) == (
        1,
        2,
        1,
    )
    assert ends["watch"]["events"] == 3 and ends["plan"]["variants"] == 5
    assert ends["schedule"]["placed"] == 2 and ends["schedule"]["extended"] == ["e002"]
    assert ends["finalize"]["verified"] == 2 and ends["finalize"]["extended"] == 1
    calls = [e for e in events if e.type == "call"]
    assert [c.data["role"] for c in calls] == tf.roles()
    assert [c.stage for c in calls] == [c["stage"] for c in tf.calls]
    assert all(c.data["cost_usd"] > 0 and c.data["tokens_in"] == 1000 for c in calls)
    cues = [e.data for e in events if e.type == "cue"]
    assert [(c["id"], c["status"], c["cached"]) for c in cues] == [
        ("c001", "verified", False),
        ("c002", "verified", False),
        ("x002", "extended", False),
    ]
    assert cues[0]["text"] == CUE_TEXT["c001"] and cues[0]["start"] == 1.8
    assert cues[0]["audio_dur"] == pytest.approx(2.0, abs=0.01) and cues[0]["note"] is None
    warnings = [e.data for e in events if e.type == "warning"]
    clamped = [w for w in warnings if w["kind"] == "clamped_word"]
    assert [(w["text"], w["end"], w["clamped_end"]) for w in clamped] == [("Ada.", 3.5, 1.6)]
    assert all(w["message"] for w in warnings)
    assert [w["kind"] for w in warnings if w["kind"] != "clamped_word"] == ["research_skipped"]
    done = events[-1]
    assert done.type == "done" and done.stage is None
    assert done.data["summary"]["cues"]["verified"] == 2
    assert done.data["output"] == str(runs["run_dir"]) and done.data["run_id"] == "t1"
    assert done.data["seconds"] > 0 and done.data["manifest"] == "manifest.json"


def test_second_run_hits_the_cache_for_every_stage(runs):
    _, _, _, first_events = runs["first"]
    _, tf, speech, events = runs["second"]
    assert tf.calls == [] and speech.calls == []  # no model, ASR or TTS call at all
    ends = _ends(events)
    assert {s: ends[s]["cached"] for s in CACHED} == dict.fromkeys(CACHED, True)
    assert ends["publish"]["cached"] is False
    assert not [e for e in events if e.type == "call"]
    cues = [(e.data["id"], e.data["status"], e.data["cached"]) for e in events if e.type == "cue"]
    assert cues == [
        ("c001", "verified", True),
        ("c002", "verified", True),
        ("x002", "extended", True),
    ]
    assert events[-1].data["summary"]["cues"] == first_events[-1].data["summary"]["cues"]
    assert len(runs["ledger"].calls("t1")) == 6  # only the first run's calls


# -- spend: the pre-run check and a provider 402 mid-run ------------------------------------------


def test_budget_estimate_and_check_before_any_stage(tone_slides, tmp_path):
    assert budget_estimate(60.0) == pytest.approx(0.07)  # $0.05 a minute + $0.02
    assert budget_estimate(12.0) == pytest.approx(0.03)
    ledger = _ledger(tmp_path, cap_run=0.05)
    ledger.record(
        CallRecord(
            run_id="tb",
            stage="plan",
            role="plan",
            provider="tokenfactory",
            model="m",
            tokens_in=0,
            tokens_cached=0,
            tokens_out=0,
            latency_s=0.0,
            cost_usd=0.03,
            ts=time.time(),
        )
    )
    speech = FakeSpeech()
    with pytest.raises(BudgetExceeded) as err:
        _run(tone_slides, tmp_path / "run", ledger, speech=speech, run_id="tb")
    assert err.value.scope == "run"  # 0.03 spent + 0.03 estimate > 0.05
    assert speech.calls == [] and not (tmp_path / "run" / "stages").exists()


@pytest.mark.parametrize("kind", ["concat", "hls"])
def test_a_crafted_container_stops_the_run_before_any_stage(tone_slides, tmp_path, kind):
    """An upload named .mp4 that is a concat list or an HLS playlist is refused right after the
    probe: no stage starts, no service is called, nothing is cached (E-sec)."""
    shutil.copy(tone_slides, tmp_path / "real.mp4")
    evil = tmp_path / "evil.mp4"
    body = (
        "ffconcat version 1.0\nfile real.mp4\nduration 12.0\n"
        if kind == "concat"
        else "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nhttp://169.254.169.254/x\n"
        "#EXT-X-ENDLIST\n"
    )
    evil.write_text(body, encoding="utf-8")
    ledger = _ledger(tmp_path)
    speech, tf, events = FakeSpeech(), FakeTF(ledger), []
    with pytest.raises(MediaError):
        run(evil, tmp_path / "run", RunOptions(), tf=tf, speech=speech, tavily=None,
            ledger=ledger, on_event=events.append, run_id="evil", wps=3.0)  # fmt: skip
    assert [e.type for e in events] == ["error"]
    assert events[0].stage is None and events[0].data["kind"] == "MediaError"
    assert tf.calls == [] and speech.calls == []
    assert not (tmp_path / "run").exists()


def _token_factory(ledger: Ledger, seen: list[str], *, fail_plan: bool) -> TokenFactory:
    """The real client against a fake server that answers 402 to the plan call."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        fmt = body["response_format"]["json_schema"]
        seen.append(fmt["name"])
        if fmt["name"] == "plan" and fail_plan:
            return httpx.Response(402, json={"error": "insufficient balance"})
        if fmt["name"] == "finalize":
            props = fmt["schema"]["properties"]
            msgs = body["messages"]
            answer = (
                verify_all({"messages": msgs}) if "claims" in props else {"text": description(msgs)}
            )
        else:
            answer = {"watch": WATCH, "research": TERMS, "plan": PLAN}[fmt["name"]]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
            },
        )

    return TokenFactory("k", ledger, transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_provider_402_at_plan_keeps_earlier_stages_and_a_rerun_resumes(
    tone_slides, tmp_path, fast_media
):
    ledger = _ledger(tmp_path)
    run_dir = tmp_path / "run"
    seen: list[str] = []
    tf = _token_factory(ledger, seen, fail_plan=True)
    events: list[Event] = []
    with pytest.raises(BudgetExceeded) as err:
        run(
            tone_slides,
            run_dir,
            RunOptions(),
            tf=tf,
            speech=FakeSpeech(),
            tavily=None,
            ledger=ledger,
            on_event=events.append,
            run_id="t402",
            wps=3.0,
        )
    assert err.value.scope == "provider"
    assert seen == ["watch", "research", "plan"]  # a 402 is never retried
    assert sorted(p.name for p in (run_dir / "stages").iterdir()) == [
        "listen.json",
        "research",  # its steps
        "research.json",
        "watch.json",
    ]
    assert (events[-1].type, events[-1].stage) == ("error", "plan")
    assert events[-1].data["kind"] == "BudgetExceeded" and "402" in events[-1].data["message"]
    assert not (run_dir / "manifest.json").exists()

    seen.clear()
    speech = FakeSpeech()
    res, *_ = _run(
        tone_slides,
        run_dir,
        ledger,
        tf=_token_factory(ledger, seen, fail_plan=False),
        speech=speech,
        run_id="t402",
    )
    assert seen == ["plan", "finalize", "finalize", "finalize"]  # resumed at plan
    assert speech.calls == ["synthesize"] * 3
    assert res.manifest.exists()


# -- wiring: one voice rate, the names, speech spans -----------------------------------------------


def _spy(monkeypatch, *names: str) -> dict[str, list[dict]]:
    """Wrap pipeline functions; record each call's arguments by parameter name."""
    seen: dict[str, list[dict]] = {n: [] for n in names}
    for name in names:
        real = getattr(pipeline, name)

        def inner(*a, _real=real, _name=name, **kw):
            seen[_name].append(dict(inspect.signature(_real).bind(*a, **kw).arguments))
            return _real(*a, **kw)

        monkeypatch.setattr(pipeline, name, inner)
    return seen


def test_one_voice_rate_names_and_speech_flow_through_the_stages(
    tone_slides, tmp_path, fast_media, monkeypatch
):
    plan = copy.deepcopy(PLAN)
    plan["cast"].append({"descriptor": "a tall person", "name": "Proog"})
    plan["items"][0]["medium"] = "Proog stands on a plain red screen."
    seen = _spy(
        monkeypatch, "listen", "find_gaps", "split_gaps", "schedule", "compose_cues",
        "finalize_cue", "publish",
    )  # fmt: skip
    ledger = _ledger(tmp_path)
    _run(tone_slides, tmp_path / "run", ledger, tf=FakeTF(ledger, plan=plan), wps=2.7)

    rates = [seen[n][0]["wps"] for n in ("listen", "find_gaps", "split_gaps", "schedule")]
    assert rates == [2.7, 2.7, 2.7, 2.7]
    compose = seen["compose_cues"][0]
    assert compose["proper_nouns"] == {"Ada", "Proog", "Lull"}  # cast + glossary canonical
    assert compose["extended"] == ["e002"]
    ctx = {c["cue"].id: c["ctx"] for c in seen["finalize_cue"]}
    assert list(ctx) == ["c001", "c002", "x002"]  # standard cues in time order, then extended
    for c in ctx.values():
        assert c.wps == 2.7 and c.proper_nouns == {"Ada", "Proog", "Lull"}
        assert c.named == {"Ada", "Proog"}  # cast + glossary people ("Lull" is a work)
        assert c.cast == {"Ada": "a presenter", "Proog": "a tall person"}
        assert c.video_duration == pytest.approx(12.0, abs=0.05)
    assert ctx["x002"].verbatim == ["Lull"] and ctx["c001"].verbatim == []
    # Ada is said at 0.99-1.6 s; c001 voices Proog
    assert ctx["c001"].introduced == {"Ada"}
    assert ctx["c002"].introduced == {"Ada", "Proog"}
    assert ctx["x002"].introduced == set()  # at 0.3 s nothing was said or described yet
    pub = seen["publish"][0]
    assert pub["speech"] == [Span(start=0.3, end=1.6), Span(start=11.2, end=11.95)]
    assert [c.id for c in pub["cues"]] == ["c001", "c002"]
    assert [c.id for c in pub["extended"]] == ["x002"]
    assert pub["cues"][0].text == "A tall person, Proog, stands on a plain red screen."
    assert [g.id for g in pub["gaps"]] == ["g001", "g002"]  # the split windows


def test_a_near_duplicate_the_plan_dropped_is_neither_placed_nor_missing(
    tone_slides, tmp_path, fast_media, monkeypatch
):
    """The plan drops e003 (its short line repeats e001's at a lower priority): the schedule
    never sees it, the report counts it with its twin, and no warning calls it undescribed."""
    plan = copy.deepcopy(PLAN)
    plan["items"][2].update(priority=3, short="A red screen.")
    seen = _spy(monkeypatch, "schedule", "placement_report", "publish")
    ledger = _ledger(tmp_path)
    res, _, _, events = _run(tone_slides, tmp_path / "run", ledger, tf=FakeTF(ledger, plan=plan))
    assert [e.id for e in seen["schedule"][0]["events"]] == ["e001", "e002"]
    assert seen["placement_report"][0]["priority"] == {"e001": 4, "e002": 5}
    assert seen["publish"][0]["duplicates"] == {"e003": "e001"}
    assert seen["publish"][0]["priority"] == {"e001": 4, "e002": 5}  # what is important
    kinds = [e.data["kind"] for e in events if e.type == "warning"]
    assert "undescribed" not in kinds
    summary = json.loads(res.manifest.read_text(encoding="utf-8"))["summary"]
    # e003 counts with e001 (c001); only e002 is missing, as fast_media makes no extended.mp4
    assert summary["coverage"]["described"] == 2
    assert summary["coverage"]["undescribed"] == ["e002"]


class _Stop(Exception):
    pass


def test_voice_rate_comes_from_calibration_or_a_flagged_default(
    tone_slides, tmp_path, home, monkeypatch
):
    rates: list[float] = []

    def listen(video, work, speech, *, wps):
        rates.append(wps)
        raise _Stop

    monkeypatch.setattr(pipeline, "listen", listen)
    home.mkdir(parents=True)
    (home / "voices.json").write_text('{"Aria": 2.5}', encoding="utf-8")
    ledger = _ledger(tmp_path)
    for voice in ("Aria", "Sofia"):
        events: list[Event] = []
        with pytest.raises(_Stop):
            run(
                tone_slides,
                tmp_path / voice,
                RunOptions(voice=voice),
                tf=FakeTF(ledger),
                speech=FakeSpeech(),
                tavily=None,
                ledger=ledger,
                on_event=events.append,
            )
        kinds = [e.data.get("kind") for e in events if e.type == "warning"]
        assert kinds == ([] if voice == "Aria" else ["voice_uncalibrated"])
    assert rates == [2.5, 3.05]


def test_finalize_resumes_cue_by_cue(tone_slides, tmp_path, fast_media):
    ledger = _ledger(tmp_path)
    run_dir = tmp_path / "run"
    _run(tone_slides, run_dir, ledger)
    (run_dir / "stages" / "finalize" / "c002.json").unlink()
    entry = json.loads((run_dir / "stages" / "finalize" / "x002.json").read_text("utf-8"))
    (run_dir / entry["data"]["audio_path"]).unlink()  # a cached cue whose audio is gone
    _, tf, speech, events = _run(tone_slides, run_dir, ledger)
    assert [description(c["messages"]) for c in tf.calls] == [CUE_TEXT["c002"], CUE_TEXT["x002"]]
    assert speech.spoken == [CUE_TEXT["c002"], CUE_TEXT["x002"]]
    cached = {e.data["id"]: e.data["cached"] for e in events if e.type == "cue"}
    assert cached == {"c001": True, "c002": False, "x002": False}
    assert _ends(events)["finalize"]["cached"] is False


def test_overlap_stops_the_run_before_publishing(tone_slides, tmp_path, fast_media, monkeypatch):
    real = pipeline.finalize_cue

    def too_long(cue, gap, *a, **kw):  # a voiced cue that runs past its pause into speech
        out = real(cue, gap, *a, **kw)
        if out.id == "c002":
            out = out.model_copy(update={"audio_dur": 6.0, "end": out.start + 6.0})
        return out

    monkeypatch.setattr(pipeline, "finalize_cue", too_long)
    events: list[Event] = []
    ledger = _ledger(tmp_path)
    with pytest.raises(OverlapError):
        run(
            tone_slides,
            tmp_path / "run",
            RunOptions(),
            tf=FakeTF(ledger),
            speech=FakeSpeech(),
            tavily=None,
            ledger=ledger,
            on_event=events.append,
            run_id="to",
            wps=3.0,
        )
    assert (events[-1].type, events[-1].stage) == ("error", "publish")
    assert not (tmp_path / "run" / "manifest.json").exists()


def test_a_failing_event_callback_never_stops_the_run(tone_slides, tmp_path, fast_media):
    def boom(event: Event) -> None:
        raise RuntimeError("the UI went away")

    ledger = _ledger(tmp_path)
    res = run(
        tone_slides,
        tmp_path / "run",
        RunOptions(),
        tf=FakeTF(ledger),
        speech=FakeSpeech(),
        tavily=None,
        ledger=ledger,
        on_event=boom,
        run_id="tf",
        wps=3.0,
    )
    assert res.manifest.exists()


def test_run_id_is_the_stem_and_the_video_hash(tone_slides):
    sha = file_sha256(tone_slides)
    assert run_id_for(tone_slides, sha) == f"tone_slides-{sha[:8]}"


# -- who has been introduced --------------------------------------------------------------------


def _ev(id_, t0, t1, verbatim=None) -> VisualEvent:
    return VisualEvent(
        id=id_,
        t_start=t0,
        t_end=t1,
        kind="on_screen_text",
        text="Text.",
        verbatim_text=verbatim,
        salience=4,
        said_in_dialogue=False,
    )


def _cue(id_, start, text, *, status="verified", gap="g001", events=("e1",), dur=1.0) -> Cue:
    voiced = status != "held"
    return Cue(
        id=id_,
        gap_id=gap,
        start=start,
        end=start + dur if voiced else None,
        text=text,
        event_ids=list(events),
        status=status,
        ahead=False,
        claims=[],
        audio_path="a.wav" if voiced else None,
        audio_dur=dur if voiced else None,
    )


def test_introduced_before_reads_dialogue_and_earlier_descriptions():
    names = {"Emo", "Proog", "Lull", "Barry", "Dr Smith"}
    words = [
        Word(text="Hello", start=1.0, end=1.4),
        Word(text="Emo.", start=1.5, end=2.0),
        Word(text="Dr", start=2.5, end=2.7),
        Word(text="Smith,", start=2.8, end=3.1),
        Word(text="Proog", start=9.0, end=9.5),  # said after the cue
    ]
    heard = [_cue("c001", 4.0, "Proog climbs."), _cue("c002", 3.0, 'A caption reads: "Lull".')]
    assert introduced_before(5.0, names, words, heard) == {"Emo", "Dr Smith", "Lull", "Proog"}
    assert introduced_before(1.8, names, words, []) == set()  # Emo ends at 2.0
    lower = [Word(text="emo", start=0.0, end=0.5), Word(text="barry's", start=0.6, end=1.0)]
    assert introduced_before(5.0, names, lower, []) == set()  # case-sensitive


def _common(**kw) -> dict:
    return {"wps": 2.4, "duration": 60.0, "cast": {}, "named": set(), "proper_nouns": set(),
            "words": [], "events": [], "speech": [], "finished": [], **kw}  # fmt: skip


def test_cue_context_counts_only_what_a_listener_heard_before_the_cue():
    events = [_ev("e1", 0.0, 5.0, "Lull 2"), _ev("e2", 6.0, 9.0), _ev("e3", 30.0, 32.0, "Barry")]
    cast = {"Proog": "a tall person", "Emo": "a young person"}
    named = {"Proog", "Emo", "Barry"}  # cast and glossary people
    proper = named | {"Lull"}
    finished = [
        _cue("c001", 2.0, "Emo waves.", status="held"),  # never voiced: introduces nobody
        _cue("c002", 6.0, "A tall person, Proog, sits."),
        _cue("x003", 7.0, "Barry nods.", status="extended", gap=None, events=("e2",)),
        _cue("c004", 40.0, "Emo runs."),  # later than the cue
    ]
    std = _cue("c005", 10.0, "Text.", events=("e1", "e2"))
    ext = _cue("x009", 10.0, "Text.", status="extended", gap=None, events=("e2", "e1"))
    common = _common(cast=cast, named=named, proper_nouns=proper, events=events, finished=finished)
    ctx = cue_context(std, **common)
    assert ctx.introduced == {"Proog"}  # the extended line is not in the standard track
    assert ctx.verbatim == ["Lull 2"]
    assert (ctx.wps, ctx.video_duration, ctx.cast) == (2.4, 60.0, cast)
    assert (ctx.named, ctx.proper_nouns) == (named, proper)
    assert cue_context(ext, **common).introduced == {"Proog", "Barry"}


def test_on_screen_text_introduces_a_name_only_once_a_description_read_it():
    """Strict I4: a lower third the listener never heard read introduces nobody."""
    events = [_ev("e1", 2.0, 5.0, "Dr Ada Lovelace"), _ev("e2", 6.0, 9.0)]
    names = {"Ada Lovelace"}
    common = _common(named=names, proper_nouns=names, events=events)
    target = _cue("c003", 8.0, "Text.", events=("e2",))
    assert cue_context(target, **common).introduced == set()
    read = _cue("c001", 2.5, 'A caption reads: "Dr Ada Lovelace".', events=("e1",))
    assert cue_context(target, **{**common, "finished": [read]}).introduced == names


def test_an_extended_cue_counts_only_what_was_said_before_its_freeze_point():
    words = [Word(text="Hello", start=4.0, end=4.5), Word(text="Emo.", start=5.0, end=5.4)]
    common = _common(named={"Emo"}, proper_nouns={"Emo"}, words=words, events=[_ev("e1", 6, 7)])
    ext = _cue("x001", 6.0, "Text.", status="extended", gap=None)
    # the picture freezes where the speech around 6.0 s began (4.0 s): "Emo" is said after it
    talk = [Span(start=4.0, end=8.0)]
    assert cue_context(ext, **{**common, "speech": talk}).introduced == set()
    assert cue_context(ext, **common).introduced == {"Emo"}  # no speech: it freezes at 6.0 s
    # speech that opens the programme is heard first: the freeze waits for its end
    early = ext.model_copy(update={"start": 5.2})
    opening = [Span(start=0.0, end=5.5)]
    assert cue_context(early, **{**common, "speech": opening}).introduced == {"Emo"}
    std = _cue("c002", 9.0, "Text.")
    assert cue_context(std, **{**common, "speech": talk}).introduced == {"Emo"}


def test_extended_cues_that_share_a_freeze_point_are_heard_in_start_order():
    """Two visuals inside one speech span both freeze where the speech began: the earlier
    extended cue is voiced first in that pause, so the later one has heard it."""
    common = _common(named={"Proog"}, proper_nouns={"Proog"}, speech=[Span(start=4.0, end=8.0)],
                     events=[_ev("e1", 5.0, 6.0), _ev("e2", 6.0, 7.0)])  # fmt: skip
    first = _cue("x001", 5.0, "A tall person, Proog, waves.", status="extended", gap=None,
                 events=("e1",))  # fmt: skip
    second = _cue("x002", 6.0, "Proog sits.", status="extended", gap=None, events=("e2",))
    ctx = cue_context(second, **{**common, "finished": [first]})
    assert ctx.introduced == {"Proog"}
    # the other way round: a cue that starts later in the same pause is not heard yet
    assert cue_context(first, **{**common, "finished": [second]}).introduced == set()
    # a standard cue playing before the freeze point is heard; one after it is not
    before = _cue("c001", 2.0, "Proog stands.")
    assert cue_context(second, **{**common, "finished": [before]}).introduced == {"Proog"}
    after = _cue("c009", 4.5, "Proog stands.")
    assert cue_context(second, **{**common, "finished": [after]}).introduced == set()


def test_salient_events_the_plan_left_out_are_flagged(tone_slides, tmp_path, fast_media):
    plan = copy.deepcopy(PLAN)
    plan["items"] = plan["items"][:2]  # nothing for e003 (salience 4, 7-12 s)
    ledger = _ledger(tmp_path)
    _, _, _, events = _run(tone_slides, tmp_path / "run", ledger, tf=FakeTF(ledger, plan=plan))
    incomplete = [e.data for e in events if e.data.get("kind") == "plan_incomplete"]
    assert [w["event_ids"] for w in incomplete] == [["e003"]]  # asked twice, still nothing
    flagged = [(e.stage, e.data) for e in events if e.data.get("kind") == "undescribed"]
    assert flagged == [
        (
            "schedule",
            {
                "kind": "undescribed",
                "event_ids": ["e003"],
                "message": "1 salient event has no description from the plan and stays "
                "undescribed: e003",
            },
        )
    ]
    assert _ends(events)["schedule"]["extended"] == ["e002", "e003"]


# -- the cache: moves, stops, option changes, new engine -------------------------------------------


def test_a_moved_run_folder_reuses_every_stage(tone_slides, tmp_path, fast_media):
    ledger = _ledger(tmp_path)
    _run(tone_slides, tmp_path / "run", ledger)
    shutil.move(tmp_path / "run", tmp_path / "moved")
    _, tf, speech, events = _run(tone_slides, tmp_path / "moved", ledger)
    assert tf.calls == [] and speech.calls == []
    ends = _ends(events)
    assert {s: ends[s]["cached"] for s in CACHED} == dict.fromkeys(CACHED, True)
    cues = json.loads((tmp_path / "moved" / "cues.json").read_text("utf-8"))
    for c in cues["cues"] + cues["extended"]:
        assert c["audio_path"].startswith("work/finalize/")  # relative to the run folder
        assert (tmp_path / "moved" / c["audio_path"]).exists()
    entry = json.loads((tmp_path / "moved" / "stages/finalize/c001.json").read_text("utf-8"))
    assert entry["data"]["audio_path"].startswith("work/finalize/c001-")  # cached relative


def test_a_spend_stop_mid_finalize_keeps_the_finished_cues(tone_slides, tmp_path, fast_media):
    ledger = _ledger(tmp_path)
    count = {"verify": 0}

    def verify(call):
        count["verify"] += 1
        if count["verify"] == 2:
            raise BudgetExceeded("provider", "Token Factory returned 402: no funds")
        return verify_all(call)

    with pytest.raises(BudgetExceeded):
        _run(tone_slides, tmp_path / "run", ledger, tf=FakeTF(ledger, verify=verify))
    _, tf, speech, events = _run(tone_slides, tmp_path / "run", ledger)
    assert [description(c["messages"]) for c in tf.calls] == [CUE_TEXT["c002"], CUE_TEXT["x002"]]
    assert speech.spoken == [CUE_TEXT["c002"], CUE_TEXT["x002"]]
    cached = {e.data["id"]: e.data["cached"] for e in events if e.type == "cue"}
    assert cached == {"c001": True, "c002": False, "x002": False}


def _cached(events: list[Event]) -> dict[str, bool]:
    return {s: _ends(events)[s]["cached"] for s in CACHED}


def test_an_option_change_reruns_only_the_stages_that_depend_on_it(
    tone_slides, tmp_path, fast_media
):
    ledger = _ledger(tmp_path)
    run_dir = tmp_path / "run"
    _run(tone_slides, run_dir, ledger)
    # another voice: only the voiced cues
    _, tf, _, events = _run(tone_slides, run_dir, ledger, opts=RunOptions(voice="Aria"))
    assert _cached(events) == {**dict.fromkeys(CACHED, True), "finalize": False}
    assert set(tf.roles()) == {"verify"}
    # a source page: research again; without Tavily nobody reads it, so the term list is the
    # kept one and nothing after research changes
    opts = RunOptions(voice="Aria", source_url="https://example.org/page")
    _, tf, _, events = _run(tone_slides, run_dir, ledger, opts=opts)
    assert _cached(events) == {**dict.fromkeys(CACHED, True), "research": False}
    assert tf.roles() == []
    # another voice rate: the transcript is kept, the room in every pause is not
    _, tf, speech, events = _run(tone_slides, run_dir, ledger, opts=opts, wps=2.5)
    assert "transcribe" not in speech.calls
    assert _cached(events) == {"listen": True, "watch": True, "research": True, "plan": False,
                               "schedule": False, "finalize": False}  # fmt: skip
    assert _ends(events)["listen"]["capacity_words"] == 22  # 2 x floor(4.45 s x 2.5)


def test_a_new_engine_or_prompt_reruns_every_stage(tone_slides, tmp_path, fast_media, monkeypatch):
    ledger = _ledger(tmp_path)
    _run(tone_slides, tmp_path / "run", ledger)
    monkeypatch.setattr(pipeline, "engine_fingerprint", lambda: "0.2.0+changed-prompts")
    _, tf, speech, events = _run(tone_slides, tmp_path / "run", ledger)
    assert _cached(events) == dict.fromkeys(CACHED, False)
    assert "transcribe" in speech.calls and "plan" in tf.roles()


def test_source_digest_follows_every_source_and_prompt_file(tmp_path):
    """Ruling (Task 16): any change to the engine's code, prompts or templates is a new
    fingerprint; compiled caches are not part of it."""
    pkg = tmp_path / "lull"
    files = {
        "__init__.py": "x = 1",
        "stages/plan.py": "def plan(): ...",
        "prompts/plan.md": "Describe.",
        "templates/report.html": "<p>",
    }
    for rel, text in files.items():
        (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / rel).write_text(text, encoding="utf-8")
    first = source_digest(pkg)
    assert first == source_digest(pkg) and len(first) == 64
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "plan.cpython-313.pyc").write_bytes(b"compiled")
    assert source_digest(pkg) == first
    seen = {first}
    for change in (
        lambda: (pkg / "stages" / "plan.py").write_text("def plan(): return 1", encoding="utf-8"),
        lambda: (pkg / "prompts" / "plan.md").write_text("Describe it.", encoding="utf-8"),
        lambda: (pkg / "templates" / "report.html").write_text("<p>x", encoding="utf-8"),
        lambda: (pkg / "stages" / "new.py").write_text("", encoding="utf-8"),
        lambda: (pkg / "stages" / "new.py").rename(pkg / "stages" / "other.py"),
    ):
        change()
        digest = source_digest(pkg)
        assert digest not in seen
        seen.add(digest)
    assert len(source_digest()) == 64  # the installed package
    pipeline.engine_fingerprint.cache_clear()  # computed once per process: the files now
    assert pipeline.engine_fingerprint() == f"{lull.__version__}+{source_digest()[:16]}"


class _BrokenTavily:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def search(self, query, *, run_id, max_results=3):
        raise TavilyError("Tavily HTTP 500")

    def extract(self, urls, *, run_id):
        return []


def test_research_with_lookup_errors_is_not_cached(tone_slides, tmp_path, fast_media, monkeypatch):
    def stop(*a, **kw):
        raise _Stop

    monkeypatch.setattr(pipeline, "plan", stop)
    ledger = _ledger(tmp_path)
    for attempt in range(2):
        tf, events = FakeTF(ledger), []
        with pytest.raises(_Stop):
            run(tone_slides, tmp_path / "run", RunOptions(), tf=tf, speech=FakeSpeech(),
                tavily=_BrokenTavily(ledger), ledger=ledger, on_event=events.append,
                run_id="tr", wps=3.0)  # fmt: skip
        # watch is cached the second time; research, which had errors, is not (its term
        # list is: only the failed lookup is tried again)
        assert tf.roles() == (["watch", "research"] if attempt == 0 else [])
        kinds = [e.data["kind"] for e in events if e.type == "warning"]
        assert [k for k in kinds if k != "clamped_word"] == ["research_error"]
        assert not (tmp_path / "run" / "stages" / "research.json").exists()


class _FlakyTavily:
    """Looks "Lull" up; "Ada" fails while `ada_fails` is set (each time with a new message)."""

    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.searched: list[str] = []
        self.ada_fails = True

    def search(self, query, *, run_id, max_results=3):
        self.searched.append(query)
        if query == "Ada" and self.ada_fails:
            raise TavilyError(f"Tavily HTTP 500 (attempt {len(self.searched)})")
        return [{"title": f"{query} (page)", "url": f"https://example.org/{query}",
                 "content": f"About {query}."}]  # fmt: skip

    def extract(self, urls, *, run_id):
        return []


def test_research_resumes_with_the_lookups_that_worked(tone_slides, tmp_path, fast_media):
    """A lookup error keeps the research stage out of the cache, but each successful lookup
    and the term list are cached on their own: a rerun only retries what failed, and while
    the glossary comes out the same the plan is not asked again."""
    terms = {"terms": [{"term": "Lull", "kind": "work", "why": "the logo"},
                       {"term": "Ada", "kind": "person", "why": "said"}]}  # fmt: skip
    ledger = _ledger(tmp_path)
    tavily = _FlakyTavily(ledger)

    def once(**kw):
        tf, events = FakeTF(ledger, research=terms), []
        run(tone_slides, tmp_path / "run", RunOptions(), tf=tf, speech=FakeSpeech(),
            tavily=tavily, ledger=ledger, on_event=events.append, run_id="tr", wps=3.0)  # fmt: skip
        return tf.roles(), [e.data["kind"] for e in events if e.type == "warning"]

    roles, kinds = once()
    assert tavily.searched == ["Lull", "Ada"]
    assert "research_error" in kinds and "research" in roles and "plan" in roles
    assert not (tmp_path / "run" / "stages" / "research.json").exists()
    roles, kinds = once()  # Ada fails again, with another message
    assert tavily.searched == ["Lull", "Ada", "Ada"]  # Lull came from the cache
    assert "research" not in roles and "plan" not in roles  # same terms, same glossary
    assert "research_error" in kinds
    tavily.ada_fails = False
    roles, kinds = once()
    assert tavily.searched == ["Lull", "Ada", "Ada", "Ada"]
    assert "plan" in roles and "research_error" not in kinds  # the glossary changed
    assert (tmp_path / "run" / "stages" / "research.json").exists()


# -- events -------------------------------------------------------------------------------------


def test_a_callback_may_cause_events_itself(tone_slides, tmp_path, fast_media, monkeypatch):
    """The callback runs under a re-entrant lock: an event it causes is delivered, no deadlock."""
    ledger = _ledger(tmp_path)
    events: list[Event] = []

    def on_event(e: Event) -> None:
        events.append(e)
        if e.type == "stage_start" and e.stage == "watch":
            ledger.record(CallRecord(run_id="tc", stage="ui", role="ui", provider="speech",
                                     model="m", tokens_in=0, tokens_cached=0, tokens_out=0,
                                     latency_s=0.0, cost_usd=0.0, ts=time.time()))  # fmt: skip

    def stop(*a, **kw):
        raise _Stop

    monkeypatch.setattr(pipeline, "research", stop)
    done = threading.Event()

    def work():
        with pytest.raises(_Stop):
            run(tone_slides, tmp_path / "run", RunOptions(), tf=FakeTF(ledger),
                speech=FakeSpeech(), tavily=None, ledger=ledger, on_event=on_event,
                run_id="tc", wps=3.0)  # fmt: skip
        done.set()

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout=60)
    assert done.is_set(), "the run deadlocked"
    kinds = [(e.type, e.stage) for e in events]
    assert kinds.index(("stage_start", "watch")) < kinds.index(("call", "ui"))


def test_a_compose_error_is_reported_as_the_schedule_stage(
    tone_slides, tmp_path, fast_media, monkeypatch
):
    def broken(*a, **kw):
        raise ValueError("schedule: unknown gap g009 for event e001")

    monkeypatch.setattr(pipeline, "compose_cues", broken)
    events: list[Event] = []
    ledger = _ledger(tmp_path)
    with pytest.raises(ValueError):
        run(tone_slides, tmp_path / "run", RunOptions(), tf=FakeTF(ledger), speech=FakeSpeech(),
            tavily=None, ledger=ledger, on_event=events.append, run_id="te", wps=3.0)  # fmt: skip
    assert (events[-1].type, events[-1].stage) == ("error", "schedule")
    assert ("stage_end", "schedule") not in [(e.type, e.stage) for e in events]


def test_source_digest_reads_only_source_files_with_unix_line_ends(tmp_path):
    """M2: only .py .md .html .json .txt files count, and CRLF reads as LF, so a checkout
    with Windows line ends or a stray editor file never changes the fingerprint."""
    pkg = tmp_path / "lull"
    (pkg / "prompts").mkdir(parents=True)
    (pkg / "a.py").write_bytes(b"x = 1\ny = 2\n")
    (pkg / "prompts" / "plan.md").write_bytes(b"Describe.\n")
    first = source_digest(pkg)
    (pkg / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")
    assert source_digest(pkg) == first
    for junk in ("a.py.bak", "notes.swp", ".DS_Store", "big.bin"):
        (pkg / junk).write_bytes(b"junk")
    assert source_digest(pkg) == first
    for kept in ("data.json", "words.txt", "page.html"):
        (pkg / kept).write_text("{}", encoding="utf-8")
        assert source_digest(pkg) != first
        first = source_digest(pkg)
