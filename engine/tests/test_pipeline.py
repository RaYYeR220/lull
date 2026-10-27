import copy
import inspect
import json
import time
from pathlib import Path

import httpx
import pytest

from lull import pipeline
from lull.config import get_settings
from lull.ledger import BudgetExceeded, CallRecord, Ledger
from lull.pipeline import Event, budget_estimate, cue_context, introduced_before, run, run_id_for
from lull.stages.finalize import OverlapError
from lull.store import file_sha256
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
    return Ledger(root / "ledger.db", **{"cap_run": 0, "cap_day": 0, "cap_total": 0, **caps})


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
    assert cues["cues"][0]["audio_path"] == "work/audio/c001.wav"  # inside the run directory
    vtt = (run_dir / "descriptions.vtt").read_text(encoding="utf-8")
    assert f"c001\n00:00:01.800 --> 00:00:03.800\n{CUE_TEXT['c001']}\n" in vtt
    assert "<html" in (run_dir / "report.html").read_text(encoding="utf-8").lower()
    assert tf.roles() == ["watch", "research", "plan", "verify", "verify", "verify"]
    assert speech.calls == ["transcribe", "diarize"] + ["synthesize"] * 3
    stages = run_dir / "stages"
    assert sorted(p.relative_to(stages).as_posix() for p in stages.rglob("*.json")) == [
        "finalize/c001.json",
        "finalize/c002.json",
        "finalize/x002.json",
        "listen.json",
        "plan.json",
        "research.json",
        "schedule.json",
        "watch.json",
    ]


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
    assert [(w["text"], w["end"], w["clamped_end"]) for w in clamped] == [("Bye.", 11.95, 11.81)]
    assert all(w["message"] for w in warnings)
    assert [w["kind"] for w in warnings if w["kind"] != "clamped_word"] == ["research_skipped"]
    done = events[-1]
    assert done.type == "done" and done.stage is None
    assert done.data["summary"]["cues"]["verified"] == 2
    assert done.data["output"] == str(runs["run_dir"]) and done.data["run_id"] == "t1"
    assert done.data["seconds"] > 0


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
        monkeypatch, "listen", "split_gaps", "schedule", "compose_cues", "finalize_cue", "publish"
    )
    ledger = _ledger(tmp_path)
    _run(tone_slides, tmp_path / "run", ledger, tf=FakeTF(ledger, plan=plan), wps=2.7)

    rates = [seen[n][0]["wps"] for n in ("listen", "split_gaps", "schedule")]
    assert rates == [2.7, 2.7, 2.7]
    compose = seen["compose_cues"][0]
    assert compose["proper_nouns"] == {"Ada", "Proog", "Lull"}  # cast + glossary canonical
    assert compose["extended"] == ["e002"]
    ctx = {c["cue"].id: c["ctx"] for c in seen["finalize_cue"]}
    assert list(ctx) == ["c001", "c002", "x002"]  # standard cues in time order, then extended
    for c in ctx.values():
        assert c.wps == 2.7 and c.named == c.proper_nouns == {"Ada", "Proog", "Lull"}
        assert c.cast == {"Ada": "a presenter", "Proog": "a tall person"}
        assert c.video_duration == pytest.approx(12.0, abs=0.05)
    assert ctx["x002"].verbatim == ["Lull"] and ctx["c001"].verbatim == []
    # Ada is said at 1.0-1.6 s and the Lull logo is on screen from 0.3 s; c001 voices Proog
    assert ctx["c001"].introduced == {"Ada", "Lull"}
    assert ctx["c002"].introduced == {"Ada", "Lull", "Proog"}
    assert ctx["x002"].introduced == set()  # at 0.3 s nothing was said, shown or described yet
    pub = seen["publish"][0]
    assert pub["speech"] == [Span(start=0.3, end=1.6), Span(start=11.2, end=11.81)]
    assert [c.id for c in pub["cues"]] == ["c001", "c002"]
    assert [c.id for c in pub["extended"]] == ["x002"]
    assert pub["cues"][0].text == "A tall person, Proog, stands on a plain red screen."
    assert [g.id for g in pub["gaps"]] == ["g001", "g002"]  # the split windows


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
    assert rates == [2.5, 2.36]


def test_finalize_resumes_cue_by_cue(tone_slides, tmp_path, fast_media):
    ledger = _ledger(tmp_path)
    run_dir = tmp_path / "run"
    _run(tone_slides, run_dir, ledger)
    (run_dir / "stages" / "finalize" / "c002.json").unlink()
    (run_dir / "work" / "audio" / "x002.wav").unlink()  # a cached cue whose audio is gone
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


def test_introduced_before_reads_dialogue_screen_text_and_earlier_descriptions():
    names = {"Emo", "Proog", "Lull", "Barry", "Dr Smith"}
    words = [
        Word(text="Hello", start=1.0, end=1.4),
        Word(text="Emo.", start=1.5, end=2.0),
        Word(text="Dr", start=2.5, end=2.7),
        Word(text="Smith,", start=2.8, end=3.1),
        Word(text="Proog", start=9.0, end=9.5),  # said after the cue
    ]
    events = [_ev("e1", 3.0, 4.0, "LULL presents Lull"), _ev("e2", 12.0, 13.0, "Barry")]
    earlier = [_cue("c001", 4.0, "Proog climbs.")]
    assert introduced_before(5.0, names, words, events, earlier) == {
        "Emo",
        "Dr Smith",
        "Lull",
        "Proog",
    }
    assert introduced_before(1.8, names, words, events, earlier) == set()  # Emo ends at 2.0
    lower = [Word(text="emo", start=0.0, end=0.5), Word(text="barry's", start=0.6, end=1.0)]
    assert introduced_before(5.0, names, lower, [], []) == set()  # case-sensitive


def test_cue_context_counts_only_what_a_listener_heard_before_the_cue():
    events = [_ev("e1", 0.0, 5.0, "Lull 2"), _ev("e2", 6.0, 9.0), _ev("e3", 30.0, 32.0, "Barry")]
    cast = {"Proog": "a tall person", "Emo": "a young person"}
    names = {"Proog", "Emo", "Lull", "Barry"}
    finished = [
        _cue("c001", 2.0, "Emo waves.", status="held"),  # never voiced: introduces nobody
        _cue("c002", 6.0, "A tall person, Proog, sits."),
        _cue("x003", 7.0, "Barry nods.", status="extended", gap=None, events=("e2",)),
        _cue("c004", 40.0, "Emo runs."),  # later than the cue
    ]
    std = _cue("c005", 10.0, "Text.", events=("e1", "e2"))
    ext = _cue("x009", 10.0, "Text.", status="extended", gap=None, events=("e2", "e1"))
    common = {
        "wps": 2.4,
        "duration": 60.0,
        "cast": cast,
        "names": names,
        "words": [],
        "events": events,
        "finished": finished,
    }
    ctx = cue_context(std, **common)
    assert ctx.introduced == {"Lull", "Proog"}  # the extended line is not in the standard track
    assert ctx.verbatim == ["Lull 2"]
    assert (ctx.wps, ctx.video_duration, ctx.cast) == (2.4, 60.0, cast)
    assert ctx.named == ctx.proper_nouns == names
    assert cue_context(ext, **common).introduced == {"Lull", "Proog", "Barry"}


def test_salient_events_the_plan_left_out_are_flagged(tone_slides, tmp_path, fast_media):
    plan = copy.deepcopy(PLAN)
    plan["items"] = plan["items"][:2]  # nothing for e003 (salience 4, 7-12 s)
    ledger = _ledger(tmp_path)
    _, _, _, events = _run(tone_slides, tmp_path / "run", ledger, tf=FakeTF(ledger, plan=plan))
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
