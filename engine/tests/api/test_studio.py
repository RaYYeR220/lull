"""Studio: edit, approve and hold the cues of a finished run, then publish it again.

The run is a real engine run on the tone_slides clip (the engine's own test doubles for the
model and speech services), built once and copied into each test's LULL_DATA. Edits go
through the real finalize_cue and publish with fast media stand-ins.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import threading
import time
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from lull.api.app import create_app
from lull.api.ratelimit import RateLimiter
from lull.api.settings import ApiSettings
from lull.ledger import BudgetExceeded, CallRecord, Ledger
from lull.pipeline import run as run_engine
from lull.types import Cue, RunOptions
from pipeline_fakes import CUE_TEXT, FakeSpeech, FakeTF, description

RID = "live-20261027-120000-abcdef"
RID2 = "live-20261027-120500-123456"


# -- the run every test starts from -------------------------------------------------------------


def _fast(mp: pytest.MonkeyPatch) -> None:
    """The conftest `fast_media` stand-ins, for a module-scoped build."""
    from lull import media
    from lull.stages import finalize as fin
    from lull.stages import mix as mixer
    from lull.stages import voice as voice_mod
    from lull.stages import watch as watch_mod

    def clip(src, dst, start, end, *, height=480):
        Path(dst).write_bytes(b"\0" * 16)
        return Path(dst)

    def seconds(path):
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / w.getframerate()

    def fake_mix(video, cues, out_dir, **kw):
        audio, vid = out_dir / mixer.AD_AUDIO, out_dir / mixer.AD_VIDEO
        audio.write_bytes(b"m4a")
        vid.write_bytes(b"mp4")
        return mixer.MixResult(ad_audio=audio, ad_video=vid)

    def fake_frames(src, shots, **kw):
        for _, d in shots:
            Path(d).write_bytes(b"\xff\xd8jpg")
        return [Path(d) for _, d in shots]

    def fake_levels(video, info, cues):
        return mixer.Levels(programme=-23.0, cues={str(c.audio_path): -20.0 for c in cues})

    mp.setattr(watch_mod, "cut_clip", clip)
    mp.setattr(watch_mod, "scene_cuts", lambda src, threshold=0.3: [])
    mp.setattr(fin, "cut_clip", clip)
    mp.setattr(fin, "audio_duration", seconds)
    mp.setattr(voice_mod, "audio_duration", seconds)
    mp.setattr(mixer, "measure_levels", fake_levels)
    mp.setattr(mixer, "mix", fake_mix)
    mp.setattr(mixer, "extended_video", lambda *a, **kw: None)
    mp.setattr(media, "frames_at", fake_frames)


@pytest.fixture(scope="module")
def template(tone_slides, tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("studio")
    run_dir = root / RID
    video = run_dir / "input" / "clip.mp4"
    video.parent.mkdir(parents=True)
    shutil.copyfile(tone_slides, video)
    (run_dir / "request.json").write_text(
        json.dumps(
            {
                "run_id": RID,
                "input": "input/clip.mp4",
                "options": {"mode": "standard", "voice": "Sofia"},
                "video": {"duration": 12.0},
            }
        )
    )
    ledger = Ledger(root / "ledger.db", cap_run=0, cap_day=0, cap_total=0)
    mp = pytest.MonkeyPatch()
    _fast(mp)
    try:
        run_engine(
            video,
            run_dir,
            RunOptions(),
            tf=FakeTF(ledger),
            speech=FakeSpeech(),
            tavily=None,
            ledger=ledger,
            run_id=RID,
            wps=3.0,
        )
    finally:
        mp.undo()
    return run_dir


def place(template: Path, root: Path, run_id: str = RID) -> Path:
    d = root / run_id
    shutil.copytree(template, d)
    return d


class Fakes:
    """What the Studio's clients factory handed out."""

    def __init__(self) -> None:
        self.answers: dict = {}
        self.tf: list[FakeTF] = []
        self.speech: list[FakeSpeech] = []

    def __call__(self, ledger):
        tf, speech = FakeTF(ledger, **self.answers), FakeSpeech()
        self.tf.append(tf)
        self.speech.append(speech)
        return tf, speech

    def roles(self) -> list[str]:
        return [r for tf in self.tf for r in tf.roles()]

    def spoken(self) -> list[str]:
        return [t for s in self.speech for t in s.spoken]


@pytest.fixture
def make_app(data, tmp_path, monkeypatch):
    monkeypatch.setenv("LULL_HOME", str(tmp_path / "home"))
    from lull.config import get_settings

    get_settings.cache_clear()

    def make(**settings):
        app = create_app(
            ApiSettings(lull_data=data, lull_web_origins="http://web.test", **settings)
        )
        app.state.live.ledger = Ledger(tmp_path / "ledger.db", cap_run=0, cap_day=0, cap_total=0)
        app.state.studio.clients = Fakes()
        return app

    yield make
    get_settings.cache_clear()


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def fakes(app) -> Fakes:
    return app.state.studio.clients


@pytest.fixture
def sc(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def rd(template, data, fast_media) -> Path:
    return place(template, data / "runs")


def cues_doc(d: Path) -> dict:
    return json.loads((d / "cues.json").read_text(encoding="utf-8"))


def cue_of(d: Path, cue_id: str) -> dict:
    doc = cues_doc(d)
    return next(c for c in doc["cues"] + doc["extended"] if c["id"] == cue_id)


def stage_entry(d: Path, cue_id: str) -> dict:
    return json.loads((d / "stages" / "finalize" / f"{cue_id}.json").read_text(encoding="utf-8"))


def snapshot(d: Path) -> dict[str, str]:
    return {
        p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(d.rglob("*"))
        if p.is_file()
    }


def patch(c, cue_id, text, run_id=RID):
    return c.patch(f"/v1/runs/{run_id}/cues/{cue_id}", json={"text": text})


NEW_RED = "A plain red screen fills the frame."


# -- PATCH: lint first ----------------------------------------------------------------------------


def test_edit_with_a_lint_error_is_refused_without_a_model_call(sc, rd, fakes, app):
    before = snapshot(rd)
    r = patch(sc, "c001", "the screen seems to glow red")
    assert r.status_code == 422
    j = r.json()
    assert j["error"] == "lint"
    assert j["text"] == "The screen seems to glow red."  # autofixed before lint
    rules = {f["rule"]: f for f in j["findings"]}
    assert rules["R12-hedge"]["severity"] == "error"
    assert rules["R12-hedge"]["span"] == [11, 19]
    assert "R12-hedge" in j["message"]
    assert fakes.tf == [] and fakes.speech == []
    assert app.state.live.ledger.calls(RID) == []
    assert snapshot(rd) == before


@pytest.mark.parametrize(
    "body",
    [{"text": ""}, {"text": "   "}, {"text": 5}, {}, {"text": "x" * 1001}, [1], "not json"],
)
def test_edit_body_must_hold_text(sc, rd, body):
    if body == "not json":
        r = sc.patch(
            f"/v1/runs/{RID}/cues/c001",
            content=b"{nope",
            headers={"content-type": "application/json"},
        )
    else:
        r = sc.patch(f"/v1/runs/{RID}/cues/c001", json=body)
    assert r.status_code == 422
    assert r.json()["error"] == "invalid"


# -- PATCH: re-checked, voiced and fitted -------------------------------------------------------


def test_edit_is_verified_voiced_and_stored(sc, rd, fakes, app):
    old_key = stage_entry(rd, "c001")["key"]
    r = patch(sc, "c001", NEW_RED)
    assert r.status_code == 200, r.text
    cue = r.json()
    assert cue["id"] == "c001" and cue["status"] == "edited" and cue["text"] == NEW_RED
    assert [(c["text"], c["verdict"]) for c in cue["claims"]] == [(NEW_RED, "supported")]
    assert "audio_path" not in cue
    assert cue["audio_url"].startswith(f"/v1/runs/{RID}/cues/c001/audio?v=")
    assert cue["start"] == 1.8 and cue["audio_dur"] == pytest.approx(7 / 3, abs=0.01)
    assert cue["end"] == pytest.approx(cue["start"] + cue["audio_dur"])
    assert isinstance(cue["findings"], list)
    assert fakes.roles() == ["verify"] and fakes.spoken() == [NEW_RED]
    calls = app.state.live.ledger.calls(RID)
    assert [(c.stage, c.role) for c in calls] == [("finalize", "verify")]

    stored = cue_of(rd, "c001")
    assert stored["status"] == "edited" and stored["text"] == NEW_RED
    assert stored["audio_path"].startswith("work/edits/") and stored["audio_path"].endswith(
        "/audio/c001.wav"
    )
    assert (rd / stored["audio_path"]).is_file()
    assert (rd / "work" / "audio" / "c001.wav").is_file()  # the published take is kept
    entry = stage_entry(rd, "c001")
    assert entry["key"] == old_key
    assert entry["data"]["status"] == "edited" and entry["data"]["text"] == NEW_RED
    assert Path(entry["data"]["audio_path"]).is_absolute()
    assert Path(entry["data"]["audio_path"]).resolve() == (rd / stored["audio_path"]).resolve()
    assert cue_of(rd, "c002")["status"] == "verified"  # the others are untouched

    detail = sc.get(f"/v1/runs/{RID}").json()
    assert detail["dirty"] is True
    c1 = next(c for c in detail["cues"]["cues"] if c["id"] == "c001")
    assert c1["text"] == NEW_RED and c1["audio_url"] == cue["audio_url"]
    audio = sc.get(cue["audio_url"])
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
    assert audio.content == (rd / stored["audio_path"]).read_bytes()
    assert sc.head(cue["audio_url"]).status_code == 200


def test_a_second_edit_replaces_the_first_take(sc, rd):
    first = patch(sc, "c001", NEW_RED).json()
    take1 = rd / cue_of(rd, "c001")["audio_path"]
    second = patch(sc, "c001", "The plain red screen stays still.").json()
    assert second["status"] == "edited" and second["audio_url"] != first["audio_url"]
    take2 = rd / cue_of(rd, "c001")["audio_path"]
    assert take2.is_file() and not take1.exists()
    assert not (rd / "work" / "edits" / take1.parent.parent.name).exists()


def test_edit_of_an_extended_cue(sc, rd, fakes):
    r = patch(sc, "x002", 'A small logo reads "Lull".')
    assert r.status_code == 200, r.text
    cue = r.json()
    assert cue["status"] == "edited" and cue["gap_id"] is None
    doc = cues_doc(rd)
    assert [c["id"] for c in doc["extended"]] == ["x002"]
    assert doc["extended"][0]["text"] == 'A small logo reads "Lull".'


def test_edit_the_frames_do_not_support_is_held_with_a_note(sc, rd, fakes, app):
    def not_visible(call):
        text = description(call["messages"])
        return {"claims": [{"text": text, "verdict": "not_visible", "at": 0.5}]}

    fakes.answers = {
        "verify": not_visible,
        "remove": lambda call: {"text": description(call["messages"])},
    }
    r = patch(sc, "c001", "A red curtain falls across the stage.")
    assert r.status_code == 200, r.text
    cue = r.json()
    assert cue["status"] == "held"
    assert "the rewrite removed nothing" in cue["note"]
    assert cue["audio_url"] is None and cue["audio_dur"] is None
    assert cue["claims"][0]["verdict"] == "not_visible"
    assert fakes.roles() == ["verify", "remove"] and fakes.spoken() == []
    stored = cue_of(rd, "c001")
    assert stored["status"] == "held" and stored["note"] == cue["note"]
    assert stage_entry(rd, "c001")["data"]["status"] == "held"
    assert not list((rd / "work" / "edits").glob("*"))  # a held take leaves no files


def test_engine_failure_is_502_with_nothing_stored(sc, rd, app, tmp_path):
    before = snapshot(rd)

    def boom(job):
        raise RuntimeError(f"speech server fell over reading {tmp_path / 'x' / 'c001.wav'}")

    app.state.studio.finalizer = boom
    r = patch(sc, "c001", NEW_RED)
    assert r.status_code == 502
    j = r.json()
    assert j["error"] == "engine" and "c001.wav" in j["message"]
    assert str(tmp_path) not in j["message"] and tmp_path.as_posix() not in j["message"]
    assert snapshot(rd) == before


def test_budget_refusal_is_402_before_any_model_call(sc, rd, app, fakes):
    ledger = Ledger(rd.parent.parent / "capped.db", cap_run=0.5, cap_day=0, cap_total=0)
    ledger.record(
        CallRecord(run_id=RID, stage="watch", role="watch", provider="tokenfactory", model="m",
                   tokens_in=1, tokens_cached=0, tokens_out=1, latency_s=0.1, cost_usd=0.495,
                   ts=time.time())
    )  # fmt: skip
    app.state.live.ledger = ledger
    before = snapshot(rd)
    r = patch(sc, "c001", NEW_RED)
    assert r.status_code == 402
    assert r.json()["error"] == "budget" and r.json()["scope"] == "run"
    assert fakes.tf == []
    assert snapshot(rd) == before


def test_budget_hit_during_the_edit_is_402(sc, rd, app):
    def capped(job):
        raise BudgetExceeded("day")

    app.state.studio.finalizer = capped
    before = snapshot(rd)
    r = patch(sc, "c001", NEW_RED)
    assert r.status_code == 402 and r.json()["scope"] == "day"
    assert snapshot(rd) == before


def test_edits_are_rate_limited_per_client(sc, rd, app):
    app.state.studio.limiter = RateLimiter(per_client=2, client_window=60, per_day=1000)
    for _ in range(2):
        assert patch(sc, "c001", "It seems to be red.").status_code == 422
    r = patch(sc, "c001", "It seems to be red.")
    assert r.status_code == 429
    assert r.json()["error"] == "rate_limited"
    assert 1 <= int(r.headers["retry-after"]) <= 60


def test_unknown_cue_and_unknown_run(sc, rd):
    assert patch(sc, "c999", NEW_RED).status_code == 404
    assert patch(sc, "c..1", NEW_RED).status_code == 404
    assert patch(sc, "c001", NEW_RED, run_id="nope").status_code == 404
    assert sc.post(f"/v1/runs/{RID}/cues/c999/approve").status_code == 404


def test_a_run_that_has_not_finished_cannot_be_edited(sc, data):
    d = data / "runs" / RID
    (d / "input").mkdir(parents=True)
    r = patch(sc, "c001", NEW_RED)
    assert r.status_code == 409 and r.json()["error"] == "not_ready"
    assert sc.post(f"/v1/runs/{RID}/publish").status_code == 409


def test_a_rerun_of_the_engine_keeps_the_edit(sc, rd, tone_slides):
    patch(sc, "c001", NEW_RED)
    sc.post(f"/v1/runs/{RID}/cues/c002/approve")
    ledger = Ledger(rd / "rerun.db", cap_run=0, cap_day=0, cap_total=0)
    tf, speech = FakeTF(ledger), FakeSpeech()
    run_engine(rd / "input" / "clip.mp4", rd, RunOptions(), tf=tf, speech=speech, tavily=None,
               ledger=ledger, run_id=RID, wps=3.0)  # fmt: skip
    assert tf.roles() == [] and speech.spoken == []  # every stage and cue from the cache
    assert cue_of(rd, "c001")["status"] == "edited" and cue_of(rd, "c001")["text"] == NEW_RED
    assert cue_of(rd, "c002")["status"] == "approved"


# -- approve / hold -------------------------------------------------------------------------------


def test_approve_and_hold_transitions(sc, rd):
    r = sc.post(f"/v1/runs/{RID}/cues/c001/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["audio_url"]
    assert cue_of(rd, "c001")["status"] == "approved"
    assert stage_entry(rd, "c001")["data"]["status"] == "approved"
    assert sc.post(f"/v1/runs/{RID}/cues/c001/approve").json()["status"] == "approved"

    r = sc.post(f"/v1/runs/{RID}/cues/c002/hold", json={"reason": "the blue is off"})
    assert r.status_code == 200
    assert (
        r.json()["status"] == "held" and r.json()["note"] == "held by a reviewer: the blue is off"
    )
    assert cue_of(rd, "c002")["status"] == "held"
    assert stage_entry(rd, "c002")["data"]["status"] == "held"

    r = sc.post(f"/v1/runs/{RID}/cues/c002/approve")
    assert r.status_code == 409
    assert r.json()["error"] == "invalid_transition" and r.json()["cue_status"] == "held"
    assert cue_of(rd, "c002")["status"] == "held"

    r = sc.post(f"/v1/runs/{RID}/cues/x002/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    r = sc.post(f"/v1/runs/{RID}/cues/x002/hold")
    assert r.status_code == 200
    assert r.json()["status"] == "held" and r.json()["note"] == "held by a reviewer"
    assert sc.get(f"/v1/runs/{RID}").json()["dirty"] is True


def test_approve_an_edited_cue_and_hold_reason_checks(sc, rd):
    patch(sc, "c001", NEW_RED)
    assert sc.post(f"/v1/runs/{RID}/cues/c001/approve").json()["status"] == "approved"
    r = sc.post(f"/v1/runs/{RID}/cues/c001/hold", json={"reason": "x" * 301})
    assert r.status_code == 422 and r.json()["error"] == "invalid"
    r = sc.post(f"/v1/runs/{RID}/cues/c001/hold", json={"reason": 5})
    assert r.status_code == 422


def test_an_engine_held_cue_cannot_be_approved(sc, rd):
    doc = cues_doc(rd)
    doc["cues"][1].update(status="held", audio_path=None, audio_dur=None, end=None, note="x")
    (rd / "cues.json").write_text(json.dumps(doc))
    r = sc.post(f"/v1/runs/{RID}/cues/c002/approve")
    assert r.status_code == 409 and r.json()["error"] == "invalid_transition"
    r = sc.post(f"/v1/runs/{RID}/cues/c002/hold")  # already held: the engine's reason stays
    assert r.status_code == 200 and r.json()["note"] == "x"


@pytest.mark.parametrize("gone", ["stages/listen.json", "stages/watch.json", "input/clip.mp4"])
def test_a_run_missing_its_records_cannot_be_edited(sc, rd, gone, fakes):
    (rd / gone).unlink()
    before = snapshot(rd)
    r = patch(sc, "c001", NEW_RED)
    assert r.status_code == 409 and r.json()["error"] == "not_editable"
    assert sc.post(f"/v1/runs/{RID}/publish").status_code == 409
    assert fakes.tf == [] and snapshot(rd) == before


# -- publish ------------------------------------------------------------------------------------


def test_publish_writes_a_new_manifest(sc, rd):
    old = json.loads((rd / "manifest.json").read_text(encoding="utf-8"))
    patch(sc, "c001", NEW_RED)
    sc.post(f"/v1/runs/{RID}/cues/c002/approve")
    r = sc.post(f"/v1/runs/{RID}/publish")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["dirty"] is False
    man = j["manifest"]
    assert man["summary"]["cues"] == {
        "verified": 0, "edited": 1, "approved": 1, "held": 0, "extended": 1,
    }  # fmt: skip
    new = json.loads((rd / "manifest.json").read_text(encoding="utf-8"))
    assert new["outputs"]["cues.json"]["sha256"] != old["outputs"]["cues.json"]["sha256"]
    sha = hashlib.sha256((rd / "cues.json").read_bytes()).hexdigest()
    assert new["outputs"]["cues.json"]["sha256"] == sha
    assert NEW_RED in (rd / "descriptions.vtt").read_text(encoding="utf-8")
    c1 = next(c for c in j["cues"]["cues"] if c["id"] == "c001")
    assert c1["status"] == "edited" and c1["audio_url"]
    assert cue_of(rd, "c001")["audio_path"].startswith("work/edits/")
    assert sc.get(f"/v1/runs/{RID}").json()["dirty"] is False
    listed = {i["id"]: i for i in sc.get("/v1/runs").json()}
    assert listed[RID]["cues"]["edited"] == 1


def test_publish_refuses_an_overlap_with_speech_and_writes_nothing(sc, rd):
    doc = cues_doc(rd)
    c2 = doc["cues"][1]
    c2.update(audio_dur=6.0, end=c2["start"] + 6.0)  # runs into "Bye." at 11.2 s
    (rd / "cues.json").write_text(json.dumps(doc))
    before = snapshot(rd)
    r = sc.post(f"/v1/runs/{RID}/publish")
    assert r.status_code == 409
    assert r.json()["error"] == "overlap" and r.json()["cue_ids"] == ["c002"]
    assert snapshot(rd) == before


def test_publish_refuses_a_cue_past_its_pause(sc, rd):
    doc = cues_doc(rd)
    c1 = doc["cues"][0]
    c1.update(audio_dur=4.6, end=c1["start"] + 4.6)  # past g001's usable end, before speech
    (rd / "cues.json").write_text(json.dumps(doc))
    before = snapshot(rd)
    r = sc.post(f"/v1/runs/{RID}/publish")
    assert r.status_code == 409 and r.json()["cue_ids"] == ["c001"]
    assert snapshot(rd) == before


def test_publish_refuses_a_voiced_cue_whose_audio_is_gone(sc, rd):
    (rd / "work" / "audio" / "c002.wav").unlink()
    before = snapshot(rd)
    r = sc.post(f"/v1/runs/{RID}/publish")
    assert r.status_code == 409
    assert r.json()["error"] == "not_voiced" and r.json()["cue_ids"] == ["c002"]
    assert snapshot(rd) == before


# -- who may change what ------------------------------------------------------------------------


def _all_writes(c, run_id=RID):
    return [
        c.patch(f"/v1/runs/{run_id}/cues/c001", json={"text": NEW_RED}),
        c.post(f"/v1/runs/{run_id}/cues/c001/approve"),
        c.post(f"/v1/runs/{run_id}/cues/c001/hold", json={"reason": "no"}),
        c.post(f"/v1/runs/{run_id}/publish"),
    ]


def test_showcase_runs_are_read_only(sc, template, data, fast_media):
    d = place(template, data / "showcase")
    before = snapshot(d)
    for r in _all_writes(sc):
        assert r.status_code == 403
        assert r.json()["error"] == "read_only"
    assert snapshot(d) == before


def test_showcase_editable_locally(make_app, template, data, fast_media):
    d = place(template, data / "showcase")
    with TestClient(make_app(lull_showcase_editable=True)) as c:
        r = c.post(f"/v1/runs/{RID}/cues/c001/approve")
        assert r.status_code == 200 and r.json()["status"] == "approved"
    assert cue_of(d, "c001")["status"] == "approved"


@pytest.mark.parametrize("same", [True, False])
def test_showcase_edit_finds_its_video_among_the_samples(
    make_app, template, data, fast_media, same
):
    """A recorded run keeps no input/: its video is the sample of the same name and hash."""
    d = place(template, data / "showcase")
    video = d / "input" / "clip.mp4"
    (data / "samples").mkdir(parents=True)
    sample = data / "samples" / "clip.mp4"
    shutil.copyfile(video, sample)
    if not same:
        with open(sample, "ab") as f:
            f.write(b"x")
    shutil.rmtree(d / "input")
    (d / "request.json").unlink()
    app = make_app(lull_showcase_editable=True)
    with TestClient(app) as c:
        r = c.patch(f"/v1/runs/{RID}/cues/c001", json={"text": NEW_RED})
    if same:
        assert r.status_code == 200 and r.json()["status"] == "edited", r.text
    else:
        assert r.status_code == 409 and r.json()["error"] == "not_editable"


def test_settings_read_the_showcase_flag(monkeypatch):
    monkeypatch.setenv("LULL_SHOWCASE_EDITABLE", "1")
    assert ApiSettings().lull_showcase_editable is True
    monkeypatch.delenv("LULL_SHOWCASE_EDITABLE")
    assert ApiSettings().lull_showcase_editable is False


def test_the_run_being_described_is_busy_others_are_not(sc, rd, app, template, data):
    place(template, data / "runs", RID2)
    live = app.state.live
    live.executor.active[RID] = object()  # RID is being described right now
    try:
        for r in _all_writes(sc):
            assert r.status_code == 409 and r.json()["error"] == "busy"
        r = sc.post(f"/v1/runs/{RID2}/cues/c001/approve")
        assert r.status_code == 200
    finally:
        live.executor.active.pop(RID)


# -- edits of one run go one at a time -------------------------------------------------------------


def _async_patch(app, *requests):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await asyncio.gather(
                *(
                    c.patch(f"/v1/runs/{rid}/cues/{cid}", json={"text": t})
                    for rid, cid, t in requests
                )
            )

    return asyncio.run(go())


def _slow_finalizer(log, other_started: threading.Event, wait: float):
    def fin(job):
        first = not log
        log.append(("start", job.cue.id, time.monotonic()))
        if first:
            saw = other_started.wait(wait)
        else:
            other_started.set()
            saw = None
        log.append(("end", job.cue.id, time.monotonic(), saw))
        return job.cue.model_copy(update={"text": job.text, "status": "verified"})

    return fin


def test_edits_of_one_run_are_serialized(app, rd):
    log: list = []
    app.state.studio.finalizer = _slow_finalizer(log, threading.Event(), 0.5)
    r1, r2 = _async_patch(app, (RID, "c001", NEW_RED), (RID, "c002", "The screen turns blue."))
    assert r1.status_code == 200 and r2.status_code == 200
    kinds = [e[0] for e in log]
    assert kinds == ["start", "end", "start", "end"]  # never two at once
    assert log[1][3] is False  # the second edit had not started while the first ran
    assert {cue_of(rd, "c001")["text"], cue_of(rd, "c002")["text"]} == {
        NEW_RED,
        "The screen turns blue.",
    }


def test_edits_of_different_runs_run_side_by_side(app, rd, template, data):
    place(template, data / "runs", RID2)
    log: list = []
    app.state.studio.finalizer = _slow_finalizer(log, threading.Event(), 10)
    r1, r2 = _async_patch(app, (RID, "c001", NEW_RED), (RID2, "c001", NEW_RED))
    assert r1.status_code == 200 and r2.status_code == 200
    first_end = next(e for e in log if e[0] == "end" and len(e) == 4 and e[3] is not None)
    assert first_end[3] is True  # the other run's edit started while this one waited


# -- cue audio ----------------------------------------------------------------------------------


def test_cue_audio_route(sc, rd):
    detail = sc.get(f"/v1/runs/{RID}").json()
    c1 = detail["cues"]["cues"][0]
    assert "audio_path" not in c1
    r = sc.get(c1["audio_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert sc.get(f"/v1/runs/{RID}/cues/c999/audio").status_code == 404
    doc = cues_doc(rd)
    doc["cues"][0]["audio_path"] = "../../outside.wav"
    doc["cues"][1]["audio_path"] = str((rd / "work" / "audio" / "c002.wav").resolve())
    (rd / "cues.json").write_text(json.dumps(doc))
    (rd.parent.parent / "outside.wav").write_bytes(b"RIFF")
    assert sc.get(f"/v1/runs/{RID}/cues/c001/audio").status_code == 404
    assert sc.get(f"/v1/runs/{RID}/cues/c002/audio").status_code == 404


def test_cue_model_round_trips_through_cues_json(rd):
    doc = cues_doc(rd)
    for c in doc["cues"] + doc["extended"]:
        Cue.model_validate(c)
    assert [c["text"] for c in doc["cues"]] == [CUE_TEXT["c001"], CUE_TEXT["c002"]]
