"""Live runs: POST /v1/runs streams a run as Server-Sent Events, GET .../events replays or
resumes it. The engine is replaced by scripted runners; probing is faked unless a test needs
ffprobe itself."""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lull.api.app import create_app
from lull.api.ratelimit import RateLimiter
from lull.ledger import BudgetExceeded, Ledger
from lull.media import MediaError, Probe
from lull.pipeline import Event

BUSY = "Another description is running. Try again in a minute, or open a recorded run."
RUN_ID = re.compile(r"^live-\d{8}-\d{6}-[0-9a-f]{6}$")


# -- fakes -------------------------------------------------------------------------------------


def fake_probe(path):
    """10 s unless the file starts with DUR=<seconds>; or unreadable when it starts with BAD."""
    head = Path(path).read_bytes()[:32]
    if head.startswith(b"BAD"):
        raise MediaError(f"ffprobe failed (exit 1): {path}: Invalid data found")
    secs = float(head[4:].split(b";")[0]) if head.startswith(b"DUR=") else 10.0
    return Probe(duration=secs, width=64, height=64, fps=25.0, has_audio=True)


def fake_trim(src, dst, seconds):
    shutil.copyfile(src, dst)
    return dst


def happy(job):
    return [
        Event("stage_start", "listen", {}, 1.0),
        Event(
            "call",
            "watch",
            {
                "role": "watch",
                "model": "nvidia/Nemotron-3.5-Super-VL",
                "provider": "tokenfactory",
                "tokens_in": 1200,
                "tokens_cached": 0,
                "tokens_out": 300,
                "cost_usd": 0.00063,
                "latency_s": 2.5,
            },
            2.0,
        ),
        Event("stage_end", "listen", {"seconds": 0.5, "cached": False, "words": 5}, 3.0),
        Event(
            "cue",
            "finalize",
            {
                "id": "c001",
                "status": "verified",
                "text": "A red screen.",
                "start": 1.8,
                "audio_dur": 1.2,
                "note": None,
                "cached": False,
            },
            4.0,
        ),
        Event(
            "done",
            None,
            {
                "run_id": job.run_id,
                "output": str(job.run_dir),
                "manifest": str(job.run_dir / "manifest.json"),
                "seconds": 3.2,
                "summary": {"cost_usd": 0.01},
            },
            5.0,
        ),
    ]


class Script:
    """A stand-in for the engine: emits `events(job)`, optionally waiting on `gate` before
    event number `gate_at`, then raises `exc(job)` or writes a manifest."""

    def __init__(
        self, events=happy, *, exc=None, manifest=True, delay=0.0, gate=None, gate_at=0
    ) -> None:
        self.events, self.exc, self.manifest = events, exc, manifest
        self.delay, self.gate, self.gate_at = delay, gate, gate_at
        self.jobs: list = []
        self.finished = threading.Event()

    def __call__(self, job, on_event) -> None:
        self.jobs.append(job)
        try:
            for i, ev in enumerate(self.events(job)):
                if self.gate is not None and i == self.gate_at:
                    assert self.gate.wait(10), "gate never opened"
                on_event(ev)
                if self.delay:
                    time.sleep(self.delay)
            if self.exc is not None:
                raise self.exc(job)
            if self.manifest:
                (job.run_dir / "manifest.json").write_text(json.dumps({"run_id": job.run_id}))
        finally:
            self.finished.set()


# -- helpers -----------------------------------------------------------------------------------


def parse_sse(text: str) -> list[dict]:
    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = [x for x in block.split("\n") if x and not x.startswith(":")]
        if not lines:
            continue
        fields = dict(x.split(": ", 1) for x in lines)
        ev = json.loads(fields["data"])
        assert fields["event"] == ev["type"] and int(fields["id"]) == ev["seq"]
        out.append(ev)
    return out


def types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


def leaks(obj, *roots: Path) -> list[str]:
    """The decoded strings in obj that hold one of `roots`, in either slash style."""
    forms = [f.lower() for r in roots for f in (str(r), r.as_posix())]
    found: list[str] = []

    def walk(x):
        if isinstance(x, str):
            if any(f in x.lower() for f in forms):
                found.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj)
    return found


def post(c, content=b"fake video", name="clip.mp4", **fields):
    files = {"file": (name, content, "video/mp4")} if content is not None else None
    return c.post("/v1/runs", files=files, data=fields or None)


def run_dirs(data: Path) -> list[Path]:
    root = data / "runs"
    return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def lines_until(lines, prefix: str) -> list[str]:
    seen = []
    for line in lines:
        seen.append(line)
        if line.startswith(prefix):
            return seen
    raise AssertionError(f"stream ended before {prefix!r}: {seen}")


def make_unfinished(data: Path, run_id="live-20261027-100000-abcdef", *, request=True) -> Path:
    d = data / "runs" / run_id
    (d / "input").mkdir(parents=True)
    (d / "input" / "clip.mp4").write_bytes(b"fake video")
    if request:
        (d / "request.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "options": {"mode": "extended", "voice": "Aria"},
                    "input": "input/clip.mp4",
                    "video": {"duration": 10.0},
                }
            )
        )
    old = [
        {"type": "run", "stage": None, "ts": 1.0, "seq": 0, "run_id": run_id},
        {"type": "stage_start", "stage": "listen", "ts": 2.0, "seq": 1},
    ]
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in old))
    return d


# -- fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def app(settings, monkeypatch, tmp_path):
    monkeypatch.setenv("LULL_HOME", str(tmp_path / "home"))
    from lull.config import get_settings

    get_settings.cache_clear()
    app = create_app(settings)
    app.state.live.probe = fake_probe
    app.state.live.trim = fake_trim
    app.state.live.runner = Script()
    yield app
    get_settings.cache_clear()


@pytest.fixture
def live(app):
    return app.state.live


@pytest.fixture
def lc(app):
    with TestClient(app) as c:
        yield c


# -- POST /v1/runs -----------------------------------------------------------------------------


def test_post_streams_run_then_engine_events_then_done(lc, live, data, tmp_path):
    r = post(lc, mode="extended", voice="Aria")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"].startswith("no-cache")
    assert r.text.startswith("id: 0\nevent: run\ndata: {")
    events = parse_sse(r.text)
    assert types(events) == ["run", "stage_start", "call", "stage_end", "cue", "done"]
    assert [e["seq"] for e in events] == list(range(6))
    run_id = events[0]["run_id"]
    assert RUN_ID.match(run_id)
    call = events[2]
    assert call["stage"] == "watch" and call["role"] == "watch"
    assert call["model"] == "nvidia/Nemotron-3.5-Super-VL"
    assert (call["tokens_in"], call["tokens_out"], call["cost_usd"], call["latency_s"]) == (
        1200,
        300,
        0.00063,
        2.5,
    )
    assert events[4]["text"] == "A red screen." and events[4]["status"] == "verified"
    done = events[-1]
    assert done["run_id"] == run_id and done["summary"] == {"cost_usd": 0.01}
    assert "output" not in done and "manifest" not in done
    assert leaks(events, tmp_path) == []

    (d,) = run_dirs(data)
    assert d.name == run_id
    stored = [json.loads(x) for x in (d / "events.jsonl").read_text("utf-8").splitlines()]
    assert stored == events
    req = json.loads((d / "request.json").read_text("utf-8"))
    assert req["options"] == {"mode": "extended", "voice": "Aria"}
    assert req["source"]["name"] == "clip.mp4" and req["source"]["kind"] == "upload"
    assert re.fullmatch(r"[0-9a-f]{16}", req["client"])
    assert "testclient" not in (d / "request.json").read_text("utf-8")
    assert (d / req["input"]).read_bytes() == b"fake video"

    (job,) = live.runner.jobs
    assert job.run_id == run_id and job.run_dir == d and job.video == d / req["input"]
    assert job.opts.mode == "extended" and job.opts.voice == "Aria"
    assert job.duration == 10.0 and job.resumed is False


def test_post_with_real_probe(lc, live, noaudio):
    from lull.api import clip

    live.probe, live.trim = clip.probe, clip.trim
    r = post(lc, noaudio.read_bytes(), name="portrait.mp4")
    assert types(parse_sse(r.text))[-1] == "done"
    assert live.runner.jobs[0].duration == pytest.approx(4.0, abs=0.1)


def test_samples_listed_and_runnable(lc, live, data):
    (data / "samples").mkdir(parents=True)
    (data / "samples" / "street.mp4").write_bytes(b"DUR=8;")
    (data / "samples" / "notes.txt").write_text("not a sample")
    assert lc.get("/v1/samples").json() == [{"id": "street"}]

    r = post(lc, None, sample="street")
    assert types(parse_sse(r.text))[-1] == "done"
    (d,) = run_dirs(data)
    req = json.loads((d / "request.json").read_text("utf-8"))
    assert req["source"] == {"kind": "sample", "id": "street", "name": "street.mp4"}
    assert (d / req["input"]).read_bytes() == b"DUR=8;"
    assert live.runner.jobs[0].duration == 8.0


@pytest.mark.parametrize("sample", ["nope", "../samples/street", "street.mp4"])
def test_unknown_sample_422(lc, data, sample):
    (data / "samples").mkdir(parents=True)
    (data / "samples" / "street.mp4").write_bytes(b"DUR=8;")
    r = post(lc, None, sample=sample)
    assert r.status_code == 422 and r.json()["error"] == "sample"
    assert run_dirs(data) == []


def test_busy_409_while_a_run_is_active(lc, live, data):
    gate = threading.Event()
    live.runner = Script(gate=gate)
    with lc.stream("POST", "/v1/runs", files={"file": ("a.mp4", b"x", "video/mp4")}) as r:
        lines = r.iter_lines()
        lines_until(lines, "event: run")
        busy = post(lc)
        assert busy.status_code == 409
        assert busy.json() == {"error": "busy", "message": BUSY}
        assert busy.headers["retry-after"] == "60"
        gate.set()
        rest = "\n".join(lines)
    assert "event: done" in rest
    assert len(run_dirs(data)) == 1
    assert post(lc).status_code == 200  # free again once the run ends


def test_budget_precheck_402_json_not_stream(lc, live, data, tmp_path):
    live.ledger = Ledger(tmp_path / "caps.db", cap_run=0, cap_day=0.001, cap_total=0)
    r = post(lc)
    assert r.status_code == 402
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {
        "error": "budget",
        "scope": "day",
        "message": "The live demo has used its model budget for today. Recorded runs still work.",
    }
    assert live.runner.jobs == [] and run_dirs(data) == []


@pytest.mark.parametrize("how", ["file_size", "content_length", "chunked"])
def test_oversize_413(lc, live, data, how):
    live.max_bytes = 1000
    if how == "file_size":  # fits the form allowance, the file itself is too big
        r = post(lc, b"x" * 1500)
    else:
        boundary = "lullboundary"
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="big.mp4"\r\nContent-Type: video/mp4\r\n\r\n'
            ).encode()
            + b"x" * 200_000
            + f"\r\n--{boundary}--\r\n".encode()
        )
        ctype = {"content-type": f"multipart/form-data; boundary={boundary}"}
        content = body if how == "content_length" else iter([body[:1000], body[1000:]])
        r = lc.post("/v1/runs", content=content, headers=ctype)
    assert r.status_code == 413
    assert r.json()["error"] == "too_large" and "MB" in r.json()["message"]
    assert live.runner.jobs == [] and run_dirs(data) == []


def test_too_long_422_names_the_duration(lc, live, data):
    r = post(lc, b"DUR=121.5;")
    assert r.status_code == 422
    j = r.json()
    assert j["error"] == "too_long" and "121.5 s" in j["message"] and "120 s" in j["message"]
    assert live.runner.jobs == [] and run_dirs(data) == []


@pytest.mark.parametrize("name", ["clip.avi", "clip.txt", "clip"])
def test_unsupported_extension_422(lc, data, name):
    r = post(lc, b"fake", name=name)
    assert r.status_code == 422 and r.json()["error"] == "format"
    assert run_dirs(data) == []


def test_unreadable_container_422_without_paths(lc, live, data, tmp_path):
    from lull.api import clip

    live.probe, live.trim = clip.probe, clip.trim
    r = post(lc, b"definitely not a video" * 10, name="clip.webm")
    assert r.status_code == 422
    j = r.json()
    assert j["error"] == "unreadable"
    assert leaks(j, tmp_path) == []
    assert run_dirs(data) == []


@pytest.mark.parametrize(
    "fields,content",
    [({}, None), ({"sample": "street"}, b"fake"), ({"mode": "silly"}, b"fake")],
)
def test_bad_form_422(lc, data, fields, content):
    (data / "samples").mkdir(parents=True)
    (data / "samples" / "street.mp4").write_bytes(b"DUR=8;")
    r = post(lc, content, **fields)
    assert r.status_code == 422 and r.json()["error"] == "invalid"
    assert run_dirs(data) == []


def test_rate_limit_429_per_client_with_retry_after(lc, live):
    assert post(lc, b"DUR=500;").status_code == 422  # a rejected upload does not count
    for _ in range(3):
        assert types(parse_sse(post(lc).text))[-1] == "done"
    r = post(lc)
    assert r.status_code == 429 and r.json()["error"] == "rate_limited"
    assert 3500 < int(r.headers["retry-after"]) <= 3600
    assert len(live.runner.jobs) == 3


def test_rate_limit_429_global_day(lc, live):
    live.limiter = RateLimiter(per_client=3, per_day=2)
    for _ in range(2):
        assert post(lc).status_code == 200
    r = post(lc)
    assert r.status_code == 429 and "today" in r.json()["message"]
    assert 86000 < int(r.headers["retry-after"]) <= 86400


def test_rate_limiter_sliding_windows():
    now = [1000.0]
    rl = RateLimiter(per_client=3, per_day=20, clock=lambda: now[0])
    a, b = rl.client_key("1.2.3.4"), rl.client_key("5.6.7.8")
    assert a != b and "1.2.3.4" not in a
    for t in (1000.0, 1010.0, 1020.0):
        now[0] = t
        assert rl.retry_after(a) is None
        rl.hit(a)
    now[0] = 1030.0
    assert rl.retry_after(a) == ("client", 3570)
    assert rl.retry_after(b) is None
    now[0] = 1000.0 + 3600.5
    assert rl.retry_after(a) is None


def test_client_ip_from_forwarded_hops(app, live):
    from starlette.requests import Request

    from lull.api.live import client_ip

    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"6.6.6.6, 1.2.3.4")],
        "client": ("10.0.0.1", 1234),
    }
    assert client_ip(Request(scope), 0) == "10.0.0.1"
    assert client_ip(Request(scope), 1) == "1.2.3.4"


# -- failures ----------------------------------------------------------------------------------


def failing(job):
    return [
        Event("stage_start", "watch", {}, 1.0),
        Event("error", "watch", {"kind": "MediaError", "message": f"bad {job.video}"}, 2.0),
    ]


def test_runner_exception_error_event_and_error_json(lc, live, data, tmp_path):
    live.runner = Script(
        failing,
        exc=lambda job: MediaError(
            f"ffmpeg failed (exit 1): {job.video}: Invalid data (see {tmp_path / 'x.log'})"
        ),
        manifest=False,
    )
    r = post(lc)
    events = parse_sse(r.text)
    assert types(events) == ["run", "stage_start", "error"]
    err = events[-1]
    assert err["stage"] == "watch" and err["kind"] == "MediaError"
    assert "Invalid data" in err["message"] and "clip.mp4" in err["message"]
    assert leaks(events, tmp_path) == [] and leaks(events, Path.home()) == []
    (d,) = run_dirs(data)
    assert json.loads((d / "error.json").read_text("utf-8")) == {
        "type": "MediaError",
        "message": err["message"],
        "stage": "watch",
    }
    # finished (error.json): a reconnect replays, it does not resume
    again = lc.get(f"/v1/runs/{d.name}/events")
    assert parse_sse(again.text) == events
    assert len(live.runner.jobs) == 1


def test_budget_exceeded_mid_run(lc, live, data):
    live.runner = Script(
        lambda job: [Event("stage_start", "plan", {}, 1.0)],
        exc=lambda job: BudgetExceeded("run", "run cap $0.50 would be exceeded"),
        manifest=False,
    )
    events = parse_sse(post(lc).text)
    err = events[-1]
    assert err["type"] == "error" and err["scope"] == "run" and err["stage"] == "plan"
    assert err["kind"] == "BudgetExceeded"
    assert "budget for this run" in err["message"]
    (d,) = run_dirs(data)
    saved = json.loads((d / "error.json").read_text("utf-8"))
    assert saved["scope"] == "run" and saved["stage"] == "plan"


def test_heartbeat_comments(lc, live):
    live.heartbeat = 0.02
    live.runner = Script(delay=0.15)
    r = post(lc)
    assert ": ping\n\n" in r.text
    assert types(parse_sse(r.text)) == ["run", "stage_start", "call", "stage_end", "cue", "done"]


# -- GET /v1/runs/{id}/events ------------------------------------------------------------------


def test_replay_finished_run(lc, live, data):
    events = parse_sse(post(lc).text)
    run_id = events[0]["run_id"]
    r = lc.get(f"/v1/runs/{run_id}/events")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert parse_sse(r.text) == events
    tail = lc.get(f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": "2"})
    assert [e["seq"] for e in parse_sse(tail.text)] == [3, 4, 5]
    assert [e["seq"] for e in parse_sse(lc.get(f"/v1/runs/{run_id}/events?after=4").text)] == [5]
    assert len(live.runner.jobs) == 1


def test_events_unknown_run_404(lc):
    assert lc.get("/v1/runs/nope/events").status_code == 404


def test_follow_active_run_live(lc, live, data):
    gate = threading.Event()
    live.runner = Script(gate=gate, gate_at=2)  # stage_start, call, then waits
    with lc.stream("POST", "/v1/runs", files={"file": ("a.mp4", b"x", "video/mp4")}) as r:
        plines = r.iter_lines()
        head = lines_until(plines, "event: call")
        run_id = json.loads(head[2].split(": ", 1)[1])["run_id"]
        with lc.stream("GET", f"/v1/runs/{run_id}/events") as g:
            glines = g.iter_lines()
            replayed = lines_until(glines, "event: call")  # what happened before we came
            gate.set()
            followed = list(glines)  # then live until the run ends
        posted = parse_sse("\n".join(head + list(plines)))
    got = parse_sse("\n".join(replayed + followed))
    assert types(got) == ["run", "stage_start", "call", "stage_end", "cue", "done"]
    assert got == posted
    assert len(live.runner.jobs) == 1


def test_resume_unfinished_run(lc, live, data):
    d = make_unfinished(data)
    r = lc.get(f"/v1/runs/{d.name}/events")
    events = parse_sse(r.text)
    assert types(events) == [
        "run",
        "stage_start",
        "run",
        "stage_start",
        "call",
        "stage_end",
        "cue",
        "done",
    ]
    assert [e["seq"] for e in events] == list(range(8))
    assert events[2]["resumed"] is True and events[2]["run_id"] == d.name
    (job,) = live.runner.jobs
    assert job.resumed is True and job.run_id == d.name and job.run_dir == d
    assert job.video == d / "input" / "clip.mp4" and job.duration == 10.0
    assert job.opts.mode == "extended" and job.opts.voice == "Aria"
    stored = [json.loads(x) for x in (d / "events.jsonl").read_text("utf-8").splitlines()]
    assert stored == events
    # finished now: replay only
    assert parse_sse(lc.get(f"/v1/runs/{d.name}/events").text) == events
    assert len(live.runner.jobs) == 1


def test_resume_busy_409(lc, live, data):
    d = make_unfinished(data)
    gate = threading.Event()
    live.runner = Script(gate=gate)
    with lc.stream("POST", "/v1/runs", files={"file": ("a.mp4", b"x", "video/mp4")}) as r:
        lines = r.iter_lines()
        lines_until(lines, "event: run")
        busy = lc.get(f"/v1/runs/{d.name}/events")
        assert busy.status_code == 409 and busy.json()["error"] == "busy"
        gate.set()
        list(lines)
    assert len(live.runner.jobs) == 1


def test_resume_budget_402(lc, live, data, tmp_path):
    d = make_unfinished(data)
    live.ledger = Ledger(tmp_path / "caps.db", cap_run=0, cap_day=0, cap_total=0.001)
    r = lc.get(f"/v1/runs/{d.name}/events")
    assert r.status_code == 402 and r.json()["scope"] == "total"
    assert live.runner.jobs == []


def test_not_resumable_without_request_404(lc, live, data):
    d = make_unfinished(data, request=False)
    assert lc.get(f"/v1/runs/{d.name}/events").status_code == 404
    assert live.runner.jobs == []


def test_showcase_replays_never_resumes(lc, live, data):
    d = data / "showcase" / "demo"
    d.mkdir(parents=True)
    old = [{"type": "run", "stage": None, "ts": 1.0, "seq": 0, "run_id": "demo"}]
    (d / "events.jsonl").write_text(json.dumps(old[0]) + "\n")
    r = lc.get("/v1/runs/demo/events")
    assert parse_sse(r.text) == old
    assert live.runner.jobs == []


def test_resume_after_a_torn_last_line(lc, live, data):
    d = make_unfinished(data)
    with open(d / "events.jsonl", "ab") as f:
        f.write(b'{"type": "stage_end", "sta')  # the process stopped mid-write
    events = parse_sse(lc.get(f"/v1/runs/{d.name}/events").text)
    assert [e["seq"] for e in events] == list(range(8))
    stored = [json.loads(x) for x in (d / "events.jsonl").read_text("utf-8").splitlines()[3:]]
    assert stored == events[2:]


# -- the real engine behind the API ------------------------------------------------------------


def test_real_pipeline_through_the_api(
    lc, live, data, tmp_path, tone_slides, fast_media, monkeypatch
):
    """The default runner (the one adapter into lull.pipeline.run) with the engine's own test
    doubles for the model and speech services: real stages, ledger calls and cues stream out."""
    from lull.api import clip
    from lull.api import live as live_mod
    from pipeline_fakes import FakeSpeech, FakeTF

    calls = []

    def tf_for(ledger):
        calls.append(ledger)
        return FakeTF(ledger)

    monkeypatch.setattr(live_mod.TokenFactory, "from_settings", staticmethod(tf_for))
    monkeypatch.setattr(live_mod.Tavily, "from_settings", staticmethod(lambda ledger: None))
    monkeypatch.setattr(live_mod.NemoSpeechHTTP, "from_settings", staticmethod(FakeSpeech))
    live.runner = live_mod._run_pipeline
    live.probe, live.trim = clip.probe, clip.trim
    live.ledger = Ledger(tmp_path / "ledger.db", cap_run=0, cap_day=0, cap_total=0)

    r = post(lc, tone_slides.read_bytes(), name="tone slides.mp4")
    events = parse_sse(r.text)
    assert events[-1]["type"] == "done", events[-1]
    run_id = events[0]["run_id"]
    starts = [e["stage"] for e in events if e["type"] == "stage_start"]
    assert starts == ["listen", "watch", "research", "plan", "schedule", "finalize", "publish"]
    model_calls = [e for e in events if e["type"] == "call"]
    assert model_calls and all(
        {"role", "model", "tokens_in", "tokens_out", "cost_usd", "latency_s"} <= set(e)
        for e in model_calls
    )
    assert any(e["type"] == "cue" for e in events)
    assert events[-1]["run_id"] == run_id and "summary" in events[-1]
    assert leaks(events, tmp_path) == []
    assert [getattr(c, "inner", c) for c in calls] == [live.ledger]
    d = data / "runs" / run_id
    assert (d / "manifest.json").exists() and not (d / "error.json").exists()
    assert json.loads((d / "manifest.json").read_text("utf-8"))["video"]["name"] == (
        "tone slides.mp4"
    )
    stored = [json.loads(x) for x in (d / "events.jsonl").read_text("utf-8").splitlines()]
    assert stored == events


# -- bounded uploads, real durations, a wall clock ---------------------------------------------


def gated_probe(entered: threading.Event, gate: threading.Event):
    def probe(path):
        entered.set()
        assert gate.wait(10), "gate never opened"
        return fake_probe(path)

    return probe


def test_concurrent_uploads_are_bounded_503(lc, live, data):
    entered, gate = threading.Event(), threading.Event()
    live.max_uploads = 1
    live.probe = gated_probe(entered, gate)
    out: dict = {}
    t = threading.Thread(target=lambda: out.setdefault("r", post(lc)))
    t.start()
    try:
        assert entered.wait(10)
        r = post(lc)
        assert r.status_code == 503
        assert r.json()["error"] == "uploads_busy" and r.json()["message"]
        assert int(r.headers["retry-after"]) > 0
        assert lc.get("/v1/runs").json() == []  # the upload in progress is not listed
    finally:
        gate.set()
        t.join(10)
    assert types(parse_sse(out["r"].text))[-1] == "done"
    assert live.uploading == 0
    assert [i["id"] for i in lc.get("/v1/runs").json()] == [run_dirs(data)[0].name]


def test_refused_uploads_count_in_their_own_window(lc, live):
    live.refusals = RateLimiter(per_client=2, client_window=3600, per_day=10**6)
    assert post(lc, b"DUR=500;").status_code == 422
    assert post(lc, b"BAD").status_code == 422
    r = post(lc)  # a good upload, but this visitor has been refused twice this hour
    assert r.status_code == 429 and r.json()["error"] == "rate_limited"
    assert 3500 < int(r.headers["retry-after"]) <= 3600
    assert live.runner.jobs == []


def test_runs_use_the_trimmed_copy_and_its_duration(lc, live, data):
    def trim(src, dst, seconds):
        assert seconds == live.max_seconds
        Path(dst).write_bytes(b"DUR=5;trimmed")
        return dst

    live.trim = trim
    r = post(lc, b"DUR=9;original")
    assert types(parse_sse(r.text))[-1] == "done"
    (job,) = live.runner.jobs
    assert job.duration == 5.0 and job.video.read_bytes() == b"DUR=5;trimmed"
    assert sorted(p.name for p in (job.run_dir / "input").iterdir()) == ["clip.mp4"]
    req = json.loads((job.run_dir / "request.json").read_text("utf-8"))
    assert req["video"]["duration"] == 5.0


def test_trimmed_copy_still_too_long_422(lc, live, data):
    def trim(src, dst, seconds):
        Path(dst).write_bytes(b"DUR=300;")
        return dst

    live.trim = trim
    r = post(lc, b"DUR=9;")
    assert r.status_code == 422 and r.json()["error"] == "too_long"
    assert "300.0 s" in r.json()["message"]
    assert run_dirs(data) == []


def test_a_container_that_understates_its_length_is_cut(lc, live, noaudio):
    """The header claims 1 s; the clip is 4 s. The run gets the first 2 s (the limit here)."""
    from lull.api import clip

    def lying(path):
        if Path(path).name.startswith(".trim-"):
            return clip.probe(path)
        return Probe(duration=1.0, width=720, height=1280, fps=25.0, has_audio=False)

    live.probe, live.trim, live.max_seconds = lying, clip.trim, 2.0
    r = post(lc, noaudio.read_bytes(), name="portrait.mp4")
    assert types(parse_sse(r.text))[-1] == "done", r.text
    (job,) = live.runner.jobs
    assert job.duration == pytest.approx(2.0, abs=0.3)
    assert clip.probe(job.video).duration == pytest.approx(2.0, abs=0.3)


def test_probe_runs_under_a_time_limit(noaudio):
    from lull.api import clip

    assert clip.probe(noaudio).duration == pytest.approx(4.0, abs=0.1)
    with pytest.raises(MediaError, match="timed out"):
        clip.probe(noaudio, timeout=0.001)


def test_concat_scripts_and_other_demuxers_are_refused(lc, live, noaudio, tmp_path):
    """An ffconcat script named .mp4 would make ffmpeg read any local file it lists."""
    from lull.api import clip

    script = tmp_path / "evil.mp4"
    script.write_text(f"ffconcat version 1.0\nfile '{noaudio.as_posix()}'\n")
    with pytest.raises(MediaError):
        clip.probe(script)
    live.probe, live.trim = clip.probe, clip.trim
    r = post(lc, script.read_bytes(), name="evil.mp4")
    assert r.status_code == 422 and r.json()["error"] == "unreadable"


def test_watchdog_stops_a_run_that_takes_too_long(lc, live, data):
    gate = threading.Event()
    live.executor.wall_clock = 0.3
    slow = Script(gate=gate, gate_at=1)  # stage_start, then hangs
    live.runner = slow
    events = parse_sse(post(lc).text)
    assert types(events) == ["run", "stage_start", "error"]
    err = events[-1]
    assert err["kind"] == "RunTimeout" and err["stage"] == "listen"
    assert "took longer than" in err["message"]
    (d,) = run_dirs(data)
    saved = json.loads((d / "error.json").read_text("utf-8"))
    assert saved == {"type": "RunTimeout", "message": err["message"], "stage": "listen"}
    (job,) = slow.jobs
    assert job.stop.is_set()

    live.runner = Script()  # the executor is free again although the thread still hangs
    assert types(parse_sse(post(lc).text))[-1] == "done"
    gate.set()
    assert slow.finished.wait(10)
    time.sleep(0.1)  # what the stuck thread said after it was stopped goes nowhere
    assert parse_sse(lc.get(f"/v1/runs/{d.name}/events").text) == events


def test_the_stopped_runs_ledger_refuses_further_spend(tmp_path):
    from lull.api.live import RunTimeout, StopLedger

    stop = threading.Event()
    inner = Ledger(tmp_path / "l.db", cap_run=0, cap_day=0, cap_total=0)
    ledger = StopLedger(inner, stop)
    ledger.check("r", 0.01)
    assert ledger.spent() == 0.0 and ledger.inner is inner
    stop.set()
    with pytest.raises(RunTimeout):
        ledger.check("r", 0.01)


# -- smaller things ----------------------------------------------------------------------------


def test_upload_limit_is_30_mb(lc, live):
    from lull.api.live import MAX_UPLOAD_BYTES

    assert MAX_UPLOAD_BYTES == live.max_bytes == 30 * 2**20
    r = lc.post(
        "/v1/runs",
        content=b"x",
        headers={
            "content-type": "multipart/form-data; boundary=b",
            "content-length": str(31 * 2**20),
        },
    )
    assert r.status_code == 413 and "30 MB" in r.json()["message"]


def test_cors_exposes_retry_after(lc):
    r = lc.get("/v1/samples", headers={"Origin": "http://web.test"})
    assert "retry-after" in r.headers["access-control-expose-headers"].lower()


def test_health_says_whether_a_live_run_is_going(lc, live, monkeypatch):
    import lull.speech

    monkeypatch.setattr(lull.speech.NemoSpeechHTTP, "health", lambda self: True)
    assert lc.get("/v1/health").json()["live_busy"] is False
    gate = threading.Event()
    live.runner = Script(gate=gate)
    with lc.stream("POST", "/v1/runs", files={"file": ("a.mp4", b"x", "video/mp4")}) as r:
        lines = r.iter_lines()
        lines_until(lines, "event: run")
        assert lc.get("/v1/health").json()["live_busy"] is True  # not from the 10 s cache
        gate.set()
        list(lines)
    assert lc.get("/v1/health").json()["live_busy"] is False


def test_replay_with_nothing_left_is_204(lc, live):
    events = parse_sse(post(lc).text)
    run_id, last = events[0]["run_id"], events[-1]["seq"]
    r = lc.get(f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": str(last)})
    assert r.status_code == 204 and r.content == b""
    assert lc.get(f"/v1/runs/{run_id}/events?after={last - 1}").status_code == 200


def test_multipart_errors_have_the_error_shape(lc, data):
    files = [("file", ("a.mp4", b"x", "video/mp4")), ("file", ("b.mp4", b"y", "video/mp4"))]
    r = lc.post("/v1/runs", files=files)
    assert r.status_code == 400
    assert set(r.json()) == {"error", "message"} and r.json()["error"] == "invalid"
    assert run_dirs(data) == []


def test_ipv6_clients_are_counted_by_their_64():
    rl = RateLimiter()
    a = rl.client_key("2001:db8:1:2:aaaa::1")
    assert a == rl.client_key("2001:db8:1:2:bbbb:cccc:dddd:2")
    assert a != rl.client_key("2001:db8:1:3::1")
    assert rl.client_key("::ffff:1.2.3.4") == rl.client_key("1.2.3.4")
    assert rl.client_key("1.2.3.4") != rl.client_key("1.2.3.5")
    assert rl.client_key("not an ip") == rl.client_key("not an ip")


def test_ids_with_a_trailing_newline_are_refused(lc, data):
    from lull.api.live import SAMPLE_RE
    from lull.api.store import CUE_ID_RE, KEYFRAME_RE, RUN_ID_RE

    (data / "samples").mkdir(parents=True)
    (data / "samples" / "street.mp4").write_bytes(b"DUR=8;")
    r = post(lc, None, sample="street\n")
    assert r.status_code == 422 and r.json()["error"] == "sample"
    for rx, ok in (
        (SAMPLE_RE, "street"),
        (RUN_ID_RE, "r1"),
        (CUE_ID_RE, "c001"),
        (KEYFRAME_RE, "c001.jpg"),
    ):
        assert rx.fullmatch(ok) and not rx.fullmatch(ok + "\n")
        assert rx.match(ok + "\n") is None  # anchored however it is called


@pytest.mark.parametrize(
    "body", ["{not json", "[]", '{"options": {"mode": "silly"}, "input": "input/clip.mp4"}']
)
def test_resume_with_a_damaged_request_json_is_404(lc, live, data, body):
    d = make_unfinished(data)
    (d / "request.json").write_text(body)
    r = lc.get(f"/v1/runs/{d.name}/events")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    assert live.runner.jobs == []
