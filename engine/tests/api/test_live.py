"""Live runs: POST /v1/runs streams a run as Server-Sent Events, GET .../events replays or
resumes it. The engine is replaced by scripted runners; probing is faked unless a test needs
ffprobe itself."""

from __future__ import annotations

import hashlib
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
from lull.ledger import BudgetExceeded, CallRecord, Ledger
from lull.media import MediaError, Probe
from lull.pipeline import Event

MP4_FORMAT = "mov,mp4,m4a,3gp,3g2,mj2"
OWNER = "owner-token-of-the-unfinished-run"
OWNER_AUTH = {"Authorization": f"Bearer {OWNER}"}
BUSY = "Another description is running. Try again in a minute, or open a recorded run."
RUN_ID = re.compile(r"^live-\d{8}-[0-9a-f]{24}$")
CLIENT = {"X-Lull-Client": "test"}  # what the site sends (any value) on POST runs and forks


# -- fakes -------------------------------------------------------------------------------------


def fake_probe(path):
    """10 s unless the file starts with DUR=<seconds>; or unreadable when it starts with BAD."""
    head = Path(path).read_bytes()[:32]
    if head.startswith(b"BAD"):
        raise MediaError(f"ffprobe failed (exit 1): {path}: Invalid data found")
    secs = float(head[4:].split(b";")[0]) if head.startswith(b"DUR=") else 10.0
    return Probe(
        duration=secs, width=64, height=64, fps=25.0, has_audio=True, format_name=MP4_FORMAT
    )


def fake_transcode(src, dst, seconds, info=None):
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


def parse_sse(text: str, *, keep_token: bool = False) -> list[dict]:
    """The events of a stream; the edit token only the POST stream carries is dropped unless
    `keep_token`, so a stream compares with what was stored."""
    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = [x for x in block.split("\n") if x and not x.startswith(":")]
        if not lines:
            continue
        fields = dict(x.split(": ", 1) for x in lines)
        ev = json.loads(fields["data"])
        assert fields["event"] == ev["type"] and int(fields["id"]) == ev["seq"]
        if not keep_token:
            ev.pop("edit_token", None)
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
                    "edit_token_sha256": hashlib.sha256(OWNER.encode()).hexdigest(),
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
    app.state.live.transcode = fake_transcode
    app.state.live.runner = Script()
    app.state.live.envelope = None  # no ffmpeg on fake videos; tests that need it set it
    yield app
    get_settings.cache_clear()


@pytest.fixture
def live(app):
    return app.state.live


@pytest.fixture
def lc(app):
    with TestClient(app, headers=CLIENT) as c:
        yield c


# -- POST /v1/runs -----------------------------------------------------------------------------


def test_post_streams_run_then_engine_events_then_done(lc, live, data, tmp_path):
    r = post(lc, mode="extended", voice="Sofia")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"].startswith("no-store")  # it carries the edit token
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
    assert done["run_id"] == run_id
    assert done["summary"]["spend"] == {
        "model_usd": 0.01, "model_usd_all_runs": None, "searches": None
    }  # fmt: skip
    assert "output" not in done and "manifest" not in done
    assert leaks(events, tmp_path) == []

    (d,) = run_dirs(data)
    assert d.name == run_id
    stored = [json.loads(x) for x in (d / "events.jsonl").read_text("utf-8").splitlines()]
    assert stored == events
    req = json.loads((d / "request.json").read_text("utf-8"))
    assert req["options"] == {"mode": "extended", "voice": "Sofia"}
    assert req["source"]["name"] == "clip.mp4" and req["source"]["kind"] == "upload"
    assert re.fullmatch(r"[0-9a-f]{16}", req["client"])
    assert "testclient" not in (d / "request.json").read_text("utf-8")
    assert (d / req["input"]).read_bytes() == b"fake video"

    (job,) = live.runner.jobs
    assert job.run_id == run_id and job.run_dir == d and job.video == d / req["input"]
    assert job.opts.mode == "extended" and job.opts.voice == "Sofia"
    assert job.duration == 10.0 and job.resumed is False


def test_post_with_real_probe(lc, live, noaudio):
    from lull.api import clip

    live.probe, live.transcode = clip.probe, clip.transcode
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
    live.ledger = Ledger(tmp_path / "caps.db", cap_run=None, cap_day=0.001, cap_total=None)
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
    assert j["error"] == "too_long" and "121.5 s" in j["message"] and "60 s" in j["message"]
    assert live.runner.jobs == [] and run_dirs(data) == []


@pytest.mark.parametrize("name", ["clip.avi", "clip.txt", "clip"])
def test_unsupported_extension_422(lc, data, name):
    r = post(lc, b"fake", name=name)
    assert r.status_code == 422 and r.json()["error"] == "format"
    assert run_dirs(data) == []


def test_unreadable_container_422_without_paths(lc, live, data, tmp_path):
    from lull.api import clip

    live.probe, live.transcode = clip.probe, clip.transcode
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


def test_rate_limiter_counts_a_day_per_visitor_too():
    now = [0.0]
    rl = RateLimiter(
        per_client=5, client_window=60, per_client_day=3, per_day=100, clock=lambda: now[0]
    )
    for _ in range(3):
        assert rl.retry_after("a") is None
        rl.hit("a")
        now[0] += 61  # past the minute each time
    stop = rl.retry_after("a")
    assert stop is not None and stop[0] == "client_day" and stop[1] > 3600
    assert rl.retry_after("b") is None
    now[0] += 86_400
    assert rl.retry_after("a") is None


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
    token = parse_sse(r.text, keep_token=True)[0]["edit_token"]
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
    # failed (error.json): its owner's reconnect replays, it does not resume; nobody else's
    assert lc.get(f"/v1/runs/{d.name}/events").status_code == 403
    again = lc.get(f"/v1/runs/{d.name}/events", headers={"Authorization": f"Bearer {token}"})
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
        first = json.loads(head[2].split(": ", 1)[1])
        run_id, owner = first["run_id"], {"Authorization": f"Bearer {first['edit_token']}"}
        with lc.stream("GET", f"/v1/runs/{run_id}/events", headers=owner) as g:
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
    r = lc.get(f"/v1/runs/{d.name}/events", headers=OWNER_AUTH)
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
    assert parse_sse(lc.get(f"/v1/runs/{d.name}/events", headers=OWNER_AUTH).text) == events
    assert len(live.runner.jobs) == 1


def test_resume_busy_409(lc, live, data):
    d = make_unfinished(data)
    gate = threading.Event()
    live.runner = Script(gate=gate)
    with lc.stream("POST", "/v1/runs", files={"file": ("a.mp4", b"x", "video/mp4")}) as r:
        lines = r.iter_lines()
        lines_until(lines, "event: run")
        busy = lc.get(f"/v1/runs/{d.name}/events", headers=OWNER_AUTH)
        assert busy.status_code == 409 and busy.json()["error"] == "busy"
        gate.set()
        list(lines)
    assert len(live.runner.jobs) == 1


def test_resume_budget_402(lc, live, data, tmp_path):
    d = make_unfinished(data)
    live.ledger = Ledger(tmp_path / "caps.db", cap_run=None, cap_day=None, cap_total=0.001)
    r = lc.get(f"/v1/runs/{d.name}/events", headers=OWNER_AUTH)
    assert r.status_code == 402 and r.json()["scope"] == "total"
    assert live.runner.jobs == []


def test_not_resumable_without_request_403(lc, live, data):
    d = make_unfinished(data, request=False)  # no request.json: nobody can show they own it
    assert lc.get(f"/v1/runs/{d.name}/events", headers=OWNER_AUTH).status_code == 403
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
    events = parse_sse(lc.get(f"/v1/runs/{d.name}/events", headers=OWNER_AUTH).text)
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
    live.probe, live.transcode = clip.probe, clip.transcode
    live.ledger = Ledger(tmp_path / "ledger.db", cap_run=None, cap_day=None, cap_total=None)

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
    live.settings.lull_proxy_hops = 1
    files = {"file": ("clip.mp4", b"fake video", "video/mp4")}

    def upload(ip):
        return lc.post("/v1/runs", files=files, headers={"X-Forwarded-For": ip})

    out: dict = {}
    t = threading.Thread(target=lambda: out.setdefault("r", upload("1.1.1.1")))
    t.start()
    try:
        assert entered.wait(10)
        r = upload("2.2.2.2")  # another visitor: only the global cap stops it
        assert r.status_code == 503
        assert r.json()["error"] == "uploads_busy" and r.json()["message"]
        assert int(r.headers["retry-after"]) > 0
        assert lc.get("/v1/runs").json() == []  # the upload in progress is not listed
    finally:
        gate.set()
        t.join(10)
    assert types(parse_sse(out["r"].text))[-1] == "done"
    assert live.uploading == 0
    assert lc.get("/v1/runs").json() == []  # an upload: only its link shows it


def test_refused_uploads_count_in_their_own_window(lc, live):
    live.refusals = RateLimiter(per_client=2, client_window=3600, per_day=10**6)
    assert post(lc, b"DUR=500;").status_code == 422
    assert post(lc, b"BAD").status_code == 422
    r = post(lc)  # a good upload, but this visitor has been refused twice this hour
    assert r.status_code == 429 and r.json()["error"] == "rate_limited"
    assert 3500 < int(r.headers["retry-after"]) <= 3600
    assert live.runner.jobs == []


def test_runs_use_the_trimmed_copy_and_its_duration(lc, live, data):
    def trim(src, dst, seconds, info):
        assert seconds == live.max_seconds and info.duration == 5.4
        Path(dst).write_bytes(b"DUR=5;trimmed")
        return dst

    live.transcode = trim
    r = post(lc, b"DUR=5.4;original")
    assert types(parse_sse(r.text))[-1] == "done"
    (job,) = live.runner.jobs
    assert job.duration == 5.0 and job.video.read_bytes() == b"DUR=5;trimmed"
    assert sorted(p.name for p in (job.run_dir / "input").iterdir()) == ["clip.mp4"]
    req = json.loads((job.run_dir / "request.json").read_text("utf-8"))
    assert req["video"]["duration"] == 5.0


def test_trimmed_copy_still_too_long_422(lc, live, data):
    def trim(src, dst, seconds, info):
        Path(dst).write_bytes(b"DUR=300;")
        return dst

    live.transcode = trim
    r = post(lc, b"DUR=9;")
    assert r.status_code == 422 and r.json()["error"] == "too_long"
    assert "300.0 s" in r.json()["message"]
    assert run_dirs(data) == []


def test_a_container_that_understates_its_length_is_refused(lc, live, noaudio):
    """The header claims 1 s; the clip is 4 s: its copy (cut at the 2 s limit here) is twice
    what the header says, so the file is refused, not cut."""
    from lull.api import clip

    def lying(path):
        if Path(path).name.startswith(".copy-"):
            return clip.probe(path)
        return Probe(
            duration=1.0, width=720, height=1280, fps=25.0, has_audio=False, format_name=MP4_FORMAT
        )

    live.probe, live.transcode, live.max_seconds = lying, clip.transcode, 2.0
    r = post(lc, noaudio.read_bytes(), name="portrait.mp4")
    assert r.status_code == 422 and r.json()["error"] == "unsupported"
    assert "length" in r.json()["message"] and live.runner.jobs == []


@pytest.mark.parametrize("copy_secs,ok", [(9.0, True), (8.5, True), (7.0, False), (12.5, False)])
def test_the_stated_length_must_match_the_copy_within_20_percent(lc, live, copy_secs, ok):
    def trim(src, dst, seconds, info):
        Path(dst).write_bytes(f"DUR={copy_secs};".encode())
        return dst

    live.transcode, live.max_seconds = trim, 60.0
    r = post(lc, b"DUR=10;")
    if ok:
        assert types(parse_sse(r.text))[-1] == "done"
    else:
        assert r.status_code == 422 and r.json()["error"] == "unsupported"


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
    live.probe, live.transcode = clip.probe, clip.transcode
    r = post(lc, script.read_bytes(), name="evil.mp4")
    assert r.status_code == 422 and r.json()["error"] == "unreadable"


def test_watchdog_stops_a_run_that_takes_too_long(lc, live, data):
    gate = threading.Event()
    live.executor.wall_clock = 0.3
    slow = Script(gate=gate, gate_at=1)  # stage_start, then hangs
    live.runner = slow
    r = post(lc)
    events = parse_sse(r.text)
    owner = {"Authorization": f"Bearer {parse_sse(r.text, keep_token=True)[0]['edit_token']}"}
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
    assert parse_sse(lc.get(f"/v1/runs/{d.name}/events", headers=owner).text) == events


def test_the_stopped_runs_ledger_refuses_further_spend(tmp_path):
    from lull.api.live import RunTimeout, StopLedger

    stop = threading.Event()
    inner = Ledger(tmp_path / "l.db", cap_run=None, cap_day=None, cap_total=None)
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
    "body,status",
    [
        ("{not json", 403),  # the owner cannot be checked
        ("[]", 403),
        (
            json.dumps(
                {
                    "options": {"mode": "silly"},
                    "input": "input/clip.mp4",
                    "edit_token_sha256": hashlib.sha256(OWNER.encode()).hexdigest(),
                }
            ),
            404,
        ),
    ],
)
def test_resume_with_a_damaged_request_json_is_refused(lc, live, data, body, status):
    d = make_unfinished(data)
    (d / "request.json").write_text(body)
    r = lc.get(f"/v1/runs/{d.name}/events", headers=OWNER_AUTH)
    assert r.status_code == status
    assert r.json()["error"] == ("forbidden" if status == 403 else "not_found")
    assert live.runner.jobs == []


# -- the upload gate: a deadline, one upload per visitor, the slot after the headers ------------


def _asgi_post_stalling(
    app, *, client=("testclient", 50000), disconnect: bool = False
) -> tuple[int | None, dict | None]:
    """POST /v1/runs whose body stops arriving after its first bytes: it stalls, or with
    `disconnect` the client goes away. (None, None) when no response was sent."""
    import asyncio

    head = b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.mp4"\r\n\r\nxx'

    async def call():
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": head, "more_body": True}
            if not disconnect:
                await asyncio.sleep(30)
            return {"type": "http.disconnect"}

        out: list = []

        async def send(message):
            out.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/runs",
            "raw_path": b"/v1/runs",
            "root_path": "",
            "query_string": b"",
            "headers": [
                (b"host", b"t"),
                (b"content-type", b"multipart/form-data; boundary=b"),
                (b"x-lull-client", b"test"),
            ],
            "client": client,
            "server": ("t", 80),
            "state": {},
        }
        try:
            await asyncio.wait_for(app(scope, receive, send), 10)
        except Exception:  # an app may raise once the client is gone
            pass
        return out

    messages = asyncio.run(call())
    start = next((m for m in messages if m["type"] == "http.response.start"), None)
    if start is None:
        return None, None
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], json.loads(body)


def test_a_stalled_body_times_out_408_and_frees_the_slot(app, live, data):
    live.upload_deadline = 0.3
    live.refusals = RateLimiter(per_client=1, client_window=3600, per_day=10**6)
    status, body = _asgi_post_stalling(app)
    assert status == 408 and body["error"] == "timeout" and body["message"]
    assert live.uploading == 0 and not live.upload_keys
    assert run_dirs(data) == []
    with TestClient(app, headers=CLIENT) as c:
        r = post(c)  # the stall counted as a refused upload
        assert r.status_code == 429 and r.json()["error"] == "rate_limited"


def test_one_upload_at_a_time_per_visitor(lc, live):
    live.settings.lull_proxy_hops = 1
    entered, gate = threading.Event(), threading.Event()
    live.probe = gated_probe(entered, gate)
    a, b = {"X-Forwarded-For": "1.1.1.1"}, {"X-Forwarded-For": "2.2.2.2"}
    files = {"file": ("a.mp4", b"x", "video/mp4")}
    t = threading.Thread(target=lambda: lc.post("/v1/runs", files=files, headers=a))
    t.start()
    try:
        assert entered.wait(10)
        r = lc.post("/v1/runs", files=files, headers=a)
        assert r.status_code == 429 and r.json()["error"] == "one_upload_at_a_time"
        assert int(r.headers["retry-after"]) > 0
        other = lc.post("/v1/runs", files={"file": ("a.avi", b"x", "video/x")}, headers=b)
        assert other.status_code == 422 and other.json()["error"] == "format"  # it got in
    finally:
        gate.set()
        t.join(10)
    assert live.uploading == 0 and not live.upload_keys


def test_bad_headers_are_refused_before_taking_a_slot(lc, live):
    live.settings.lull_proxy_hops = 1
    live.max_uploads = 1
    entered, gate = threading.Event(), threading.Event()
    live.probe = gated_probe(entered, gate)
    files = {"file": ("a.mp4", b"x", "video/mp4")}
    t = threading.Thread(
        target=lambda: lc.post("/v1/runs", files=files, headers={"X-Forwarded-For": "1.1.1.1"})
    )
    t.start()
    try:
        assert entered.wait(10)  # the one slot is taken
        big = lc.post(
            "/v1/runs",
            content=b"x",
            headers={
                "X-Forwarded-For": "2.2.2.2",
                "content-type": "multipart/form-data; boundary=b",
                "content-length": str(31 * 2**20),
            },
        )
        assert big.status_code == 413  # not 503: the headers were checked first
        odd = lc.post(
            "/v1/runs",
            content=b"{}",
            headers={"X-Forwarded-For": "3.3.3.3", "content-type": "application/json"},
        )
        assert odd.status_code == 422 and odd.json()["error"] == "invalid"
        full = lc.post("/v1/runs", files=files, headers={"X-Forwarded-For": "4.4.4.4"})
        assert full.status_code == 503
    finally:
        gate.set()
        t.join(10)
    assert live.uploading == 0 and not live.upload_keys


@pytest.mark.parametrize("how", ["file_size", "format", "too_long", "unreadable"])
def test_every_refusal_frees_the_upload_slot(lc, live, how):
    content, name = {
        "file_size": (b"x" * 1500, "a.mp4"),
        "format": (b"x", "a.avi"),
        "too_long": (b"DUR=500;", "a.mp4"),
        "unreadable": (b"BAD", "a.mp4"),
    }[how]
    live.max_bytes = 1000
    assert post(lc, content, name=name).status_code in (413, 422)
    assert live.uploading == 0 and not live.upload_keys


# -- the edit token ------------------------------------------------------------------------------


def test_post_gives_the_edit_token_to_the_poster_only(lc, live, data):
    import hashlib

    events = parse_sse(post(lc).text, keep_token=True)
    token = events[0]["edit_token"]
    assert isinstance(token, str) and len(token) >= 40
    assert all("edit_token" not in e for e in events[1:])
    (d,) = run_dirs(data)
    stored = (d / "events.jsonl").read_text("utf-8")
    assert token not in stored and "edit_token" not in stored
    req = json.loads((d / "request.json").read_text("utf-8"))
    assert req["edit_token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in json.dumps(req)
    replay = parse_sse(lc.get(f"/v1/runs/{d.name}/events").text, keep_token=True)
    assert all("edit_token" not in e for e in replay)
    again = parse_sse(post(lc).text, keep_token=True)[0]["edit_token"]
    assert again != token


def test_a_resumed_run_hands_out_no_token(lc, live, data):
    d = make_unfinished(data)
    events = parse_sse(
        lc.get(f"/v1/runs/{d.name}/events", headers=OWNER_AUTH).text, keep_token=True
    )
    assert all("edit_token" not in e for e in events)


def test_cors_allows_the_authorization_header_and_only_the_ones_in_use(lc):
    def preflight(headers):
        return lc.options(
            "/v1/runs/r1/cues/c001",
            headers={
                "Origin": "http://web.test",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": headers,
            },
        )

    r = preflight("authorization, content-type, last-event-id, x-lull-client")
    assert r.status_code == 200
    allowed = r.headers["access-control-allow-headers"].lower()
    assert {"authorization", "content-type", "last-event-id", "x-lull-client"} <= {
        h.strip() for h in allowed.split(",")
    }
    assert preflight("x-something-else").status_code == 400


# -- refusals, engine timeouts, stopped runs -------------------------------------------------------


def test_a_client_that_disconnects_mid_body_counts_as_refused(app, live, data):
    live.refusals = RateLimiter(per_client=1, client_window=3600, per_day=10**6)
    status, body = _asgi_post_stalling(app, disconnect=True)
    assert status in (None, 400)
    assert live.uploading == 0 and not live.upload_keys
    assert run_dirs(data) == []
    with TestClient(app, headers=CLIENT) as c:
        r = post(c)  # the cut-off upload counted against this visitor
        assert r.status_code == 429 and r.json()["error"] == "rate_limited"


def test_any_failure_inside_the_slot_counts_as_refused(lc, live):
    from lull.api import live as live_mod

    live.refusals = RateLimiter(per_client=1, client_window=3600, per_day=10**6)

    def broken(*a, **kw):
        raise RuntimeError("something odd in the form")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(live_mod, "_options", broken)
        with pytest.raises(RuntimeError):
            post(lc)
    assert live.uploading == 0 and not live.upload_keys
    assert post(lc).status_code == 429


def test_budget_and_busy_refusals_do_not_count(lc, live, tmp_path):
    live.refusals = RateLimiter(per_client=1, client_window=3600, per_day=10**6)
    live.ledger = Ledger(tmp_path / "caps.db", cap_run=None, cap_day=0.001, cap_total=None)
    assert post(lc).status_code == 402
    live.ledger = Ledger(tmp_path / "free.db", cap_run=None, cap_day=None, cap_total=None)
    assert post(lc).status_code == 200  # the 402 was not this visitor's doing


def test_a_storage_timeout_while_saving_is_not_the_visitors_408(lc, live):
    import errno

    from lull.api import live as live_mod

    live.refusals = RateLimiter(per_client=1, client_window=3600, per_day=10**6)

    def stuck(src, dst, limit):
        raise OSError(errno.ETIMEDOUT, "Connection timed out")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(live_mod, "_save", stuck)
        r = post(lc)
    assert r.status_code == 503 and r.json()["error"] == "storage"
    assert "retry-after" in r.headers
    assert post(lc).status_code == 200  # ours, not the visitor's: not counted


def test_an_engine_timeout_is_the_engines_error_not_the_wall_clock(lc, live, data):
    live.runner = Script(
        lambda job: [Event("stage_start", "listen", {}, 1.0)],
        exc=lambda job: TimeoutError("the speech server did not answer in 300 s"),
        manifest=False,
    )
    events = parse_sse(post(lc).text)
    err = events[-1]
    assert err["type"] == "error" and err["kind"] == "TimeoutError"
    assert "speech server did not answer" in err["message"]
    assert not live.runner.jobs[0].stop.is_set()
    # it ends cleanly: the run reads failed, the executor is free for the next one
    assert not live.executor.busy
    assert lc.get(f"/v1/runs/{events[0]['run_id']}").json()["status"] == "failed"
    live.runner = Script()
    assert types(parse_sse(post(lc).text))[-1] == "done"


def test_a_stopped_run_skips_the_after_run_steps(lc, live, data):
    seen: list = []
    live.executor.after_run.append(lambda job: seen.append(job.run_id) or None)
    gate = threading.Event()
    live.executor.wall_clock = 0.3
    slow = Script(gate=gate, gate_at=1)
    live.runner = slow
    assert parse_sse(post(lc).text)[-1]["kind"] == "RunTimeout"
    gate.set()
    assert slow.finished.wait(10)
    time.sleep(0.2)
    assert seen == []  # the stopped run's thread finished, but its hooks did not run
    live.runner = Script()
    parse_sse(post(lc).text)
    assert len(seen) == 1


# -- who may resume, how long a video may be, which containers ---------------------------------


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer someone-else"}, {"Authorization": OWNER}]
)
def test_resuming_an_unfinished_run_needs_its_owner_token(lc, live, data, headers):
    d = make_unfinished(data)
    r = lc.get(f"/v1/runs/{d.name}/events", headers=headers)
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
    assert live.runner.jobs == []
    ok = lc.get(f"/v1/runs/{d.name}/events", headers=OWNER_AUTH)
    assert ok.status_code == 200 and len(live.runner.jobs) == 1


def test_replaying_a_finished_run_needs_no_token(lc, live, data):
    events = parse_sse(post(lc).text)
    with TestClient(lc.app) as anyone:
        assert parse_sse(anyone.get(f"/v1/runs/{events[0]['run_id']}/events").text) == events


def test_uploads_take_up_to_60_s(lc, live):
    assert live.max_seconds == 60.0
    assert post(lc, b"DUR=59.5;").status_code == 200
    r = post(lc, b"DUR=60.5;")
    assert r.status_code == 422 and "60 s" in r.json()["message"]


@pytest.mark.parametrize("secs,ok", [(44.0, True), (46.0, False)])
def test_samples_take_up_to_45_s(lc, live, data, secs, ok):
    assert live.max_sample_seconds == 45.0
    (data / "samples").mkdir(parents=True)
    (data / "samples" / "street.mp4").write_bytes(f"DUR={secs};".encode())
    seen = []
    live.transcode = lambda src, dst, limit, info: (
        seen.append(limit) or fake_transcode(src, dst, limit)
    )
    r = post(lc, None, sample="street")
    if ok:
        assert r.status_code == 200 and seen == [45.0], r.text
    else:
        assert r.status_code == 422 and "45 s" in r.json()["message"]


@pytest.mark.parametrize(
    "fmt,ok",
    [
        ("mov,mp4,m4a,3gp,3g2,mj2", True),
        ("matroska,webm", True),
        ("avi", False),
        ("concat", False),
        ("", False),
    ],
)
def test_only_mp4_mov_and_matroska_containers_are_taken(lc, live, fmt, ok):
    def probe(path):
        return Probe(duration=10.0, width=64, height=64, fps=25.0, has_audio=True, format_name=fmt)

    live.probe = probe
    r = post(lc)
    assert (r.status_code == 200) is ok
    if not ok:
        assert r.status_code == 422 and r.json()["error"] == "unreadable"


def test_the_engine_container_check_is_used_when_it_exists(lc, live, monkeypatch):
    from lull import media

    seen = []

    def engine_check(probe):
        seen.append(probe)
        raise media.MediaError("not a container we take")

    monkeypatch.setattr(media, "assert_safe_container", engine_check)  # it must exist
    r = post(lc)
    assert r.status_code == 422 and r.json()["error"] == "unreadable"
    assert len(seen) == 1 and seen[0].format_name == MP4_FORMAT


def test_the_real_probe_reports_the_container(noaudio):
    from lull import media
    from lull.api import clip

    info = clip.probe(noaudio)
    # the engine's own Probe, extended (not copied) with what the limits look at
    assert isinstance(info, media.Probe) and type(info).__mro__[1] is media.Probe
    assert (info.videos, info.others) == (((720, 1280, 25.0),), ())
    assert info.format_name == MP4_FORMAT
    media.assert_safe_container(info)
    assert not hasattr(clip, "SAFE_CONTAINERS") and not hasattr(clip, "ClipProbe")


def test_done_shows_model_spend_and_searches(lc, live):
    groups = [
        {
            "role": "plan",
            "model": "nvidia/Nemotron-3-Ultra-550b-a55b",
            "calls": 1,
            "cost_usd": 0.01,
        },
        {"role": "research", "model": "tavily-search", "calls": 3, "cost_usd": 0.024},
    ]
    summary = {
        "cost_usd": 0.01,
        "spend": {"model_usd_this_run": 0.01, "tavily_searches_this_run": 3,
                  "tavily_notional_usd_this_run": 0.024, "model_usd_all_runs": 0.01},
        "calls_by_role_model": groups,
    }  # fmt: skip

    def events(job):
        return [Event("done", None, {"run_id": job.run_id, "summary": summary}, 1.0)]

    live.runner = Script(events)
    r = post(lc)
    done = parse_sse(r.text)[-1]
    assert done["summary"]["spend"] == {
        "model_usd": 0.01,
        "model_usd_all_runs": 0.01,
        "searches": 3,
    }
    assert "notional" not in r.text


def test_live_runs_make_at_most_3_web_searches(lc, live):
    assert live.ledger.tavily_run == 3
    post(lc)
    (job,) = live.runner.jobs
    assert job.ledger.tavily_run == 3


# -- GET /v1/runs/{id}: a durable status for any run -----------------------------------------------


def test_every_run_has_a_status_and_a_source(lc, live, data):
    stopped = make_unfinished(data)
    j = lc.get(f"/v1/runs/{stopped.name}").json()
    # unfinished: others see that it exists and where it stands, nothing of its content
    assert set(j) == {"id", "status", "source", "created"}
    assert j["status"] == "stopped" and j["source"] == {"kind": "upload"}
    mine = lc.get(f"/v1/runs/{stopped.name}", headers=OWNER_AUTH).json()
    assert mine["status"] == "stopped" and mine["manifest"] is None and mine["cues"] is None
    assert mine["source"] == {"kind": "upload"} and mine["error"] is None
    assert mine["original"] is None  # served once the run is finished

    live.runner = Script(
        lambda job: [Event("stage_start", "watch", {}, 1.0)],
        exc=lambda job: MediaError("ffmpeg failed (exit 1): Invalid data"),
        manifest=False,
    )
    first = parse_sse(post(lc).text, keep_token=True)[0]
    failed, token = first["run_id"], first["edit_token"]
    assert set(lc.get(f"/v1/runs/{failed}").json()) == {"id", "status", "source", "created"}
    j = lc.get(f"/v1/runs/{failed}", headers={"Authorization": f"Bearer {token}"}).json()
    assert j["status"] == "failed"
    assert j["error"] == {"type": "MediaError", "message": j["error"]["message"], "stage": "watch"}

    live.runner = Script()
    done = parse_sse(post(lc).text)[0]["run_id"]
    j = lc.get(f"/v1/runs/{done}").json()
    assert j["status"] == "done" and "speech" in j  # published: anyone with the id

    gate = threading.Event()
    live.runner = Script(gate=gate)
    with lc.stream("POST", "/v1/runs", files={"file": ("a.mp4", b"x", "video/mp4")}) as r:
        lines = r.iter_lines()
        head = lines_until(lines, "event: run")
        running = json.loads(next(lines).split(": ", 1)[1])["run_id"]
        assert head and lc.get(f"/v1/runs/{running}").json()["status"] == "running"
        gate.set()
        list(lines)
    statuses = {i["id"]: i["status"] for i in lc.get("/v1/runs").json()}
    assert statuses.get(done) in (None, "done")  # the list speaks the same words


def test_the_original_is_served_for_finished_runs_only(lc, live, data):
    stopped = make_unfinished(data)
    for headers in ({}, OWNER_AUTH):
        assert lc.get(f"/v1/runs/{stopped.name}/original", headers=headers).status_code == 404
    run_id = parse_sse(post(lc).text)[0]["run_id"]  # the fake engine publishes
    r = lc.get(f"/v1/runs/{run_id}/original")
    assert r.status_code == 200 and r.content == b"fake video"


def test_loudness_is_kept_when_a_live_run_publishes_and_only_read_after(
    lc, live, data, monkeypatch
):
    from lull.api import facts

    kept: list[str] = []

    def envelope(video, length):
        kept.append(video.name)
        return [-20.0] * 3

    monkeypatch.setattr(facts, "envelope", envelope)
    live.envelope = facts.keep_loudness
    run_id = parse_sse(post(lc).text)[0]["run_id"]
    assert kept == ["clip.mp4"]  # once, when the run published
    assert (data / "runs" / run_id / "loudness.json").is_file()

    def no_ffmpeg(video, length):
        raise AssertionError("worked out on a request")

    monkeypatch.setattr(facts, "envelope", no_ffmpeg)
    assert lc.get(f"/v1/runs/{run_id}").json()["loudness"] == [-20.0] * 3

    live.runner = Script(manifest=False)  # not published: nothing kept
    parse_sse(post(lc).text)
    assert kept == ["clip.mp4"]


def test_a_sample_run_says_which_sample(lc, live, data):
    (data / "samples").mkdir(parents=True)
    (data / "samples" / "street.mp4").write_bytes(b"DUR=8;")
    run_id = parse_sse(post(lc, None, sample="street").text)[0]["run_id"]
    j = lc.get(f"/v1/runs/{run_id}").json()
    assert j["source"] == {"kind": "sample", "sample_id": "street"}


def test_a_live_run_records_its_voice_rate(lc, live, data):
    run_id = parse_sse(post(lc).text)[0]["run_id"]
    req = json.loads((data / "runs" / run_id / "request.json").read_text("utf-8"))
    assert req["wps"] == 3.05  # Sofia's built-in rate: what the engine will use
    assert lc.get(f"/v1/runs/{run_id}").json()["wps"] == 3.05


# -- uploads stay private to their link ------------------------------------------------------------


def test_the_public_list_shows_the_recorded_runs_only(lc, live, data, make_run):
    (data / "samples").mkdir(parents=True)
    (data / "samples" / "street.mp4").write_bytes(b"DUR=8;")
    upload = parse_sse(post(lc).text)[0]["run_id"]
    sample = parse_sse(post(lc, None, sample="street").text)[0]["run_id"]
    make_run(data / "showcase", "show1")
    fork_of_upload = lc.post(f"/v1/runs/{upload}/fork").json()["run_id"]
    fork_of_sample = lc.post(f"/v1/runs/{sample}/fork").json()["run_id"]
    make_run(data / "runs", "legacy", source=None)  # says nothing about itself: private
    listed = {i["id"] for i in lc.get("/v1/runs").json()}
    assert listed == {"show1"}  # the official (recorded) sample runs, nothing a visitor made
    for rid in (upload, sample, fork_of_upload, fork_of_sample, "legacy"):  # open by their id
        assert lc.get(f"/v1/runs/{rid}").json()["status"] == "done"


def test_health_states_how_long_uploads_are_kept(lc):
    j = lc.get("/v1/health").json()
    assert j["retention_days"] == 7 and j["fork_retention_hours"] == 24


# -- samples: licensed, at most 45 s, described by samples.json ------------------------------------

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"  # engine/samples, in the image


def test_samples_metadata_comes_from_samples_json(lc, data):
    root = data / "samples"
    root.mkdir(parents=True)
    (root / "street.mp4").write_bytes(b"DUR=8;")
    entries = [
        {"id": "street", "file": "street.mp4", "title": "A street", "credit": "Someone",
         "licence": "CC BY 4.0", "source_url": "https://example.org/street", "duration": 8.0},
        {"id": "gone", "file": "gone.mp4", "title": "Missing file", "licence": "CC0"},
        {"id": "../x", "file": "x.mp4", "title": "Bad id"},
        {"id": "other", "file": "street.mp4", "title": "File of another id"},
    ]  # fmt: skip
    (root / "samples.json").write_text(json.dumps({"samples": entries}))
    assert lc.get("/v1/samples").json() == [entries[0]]


def test_samples_dir_can_be_set_apart_from_the_data(tmp_path):
    from lull.api.settings import ApiSettings

    s = ApiSettings(lull_data=tmp_path / "data", lull_samples=tmp_path / "baked")
    assert s.samples_dir == tmp_path / "baked"
    assert ApiSettings(lull_data=tmp_path / "data").samples_dir == tmp_path / "data" / "samples"


def test_the_shipped_samples_are_licensed_short_and_small():
    from lull import media

    doc = json.loads((SAMPLES_DIR / "samples.json").read_text(encoding="utf-8"))
    ids = [e["id"] for e in doc["samples"]]
    assert ids == ["bbb45", "ed45", "tos45"]  # Blender open movies only: no third-party music
    for e in doc["samples"]:
        f = SAMPLES_DIR / e["file"]
        assert e["file"] == f"{e['id']}.mp4" and f.is_file()
        assert f.stat().st_size <= 6 * 1024 * 1024
        assert (
            e["title"] and e["credit"] and e["licence"] and e["source_url"].startswith("https://")
        )
        info = media.probe(f)
        media.assert_safe_container(info)
        assert info.duration <= 45.0 and abs(info.duration - e["duration"]) < 0.05
        assert info.height <= 480 and info.video_codec == "h264" and info.audio_codec == "aac"
    allowed = (SAMPLES_DIR.parent / ".dockerignore").read_text(encoding="utf-8")
    assert "!samples/" in allowed.splitlines()  # baked into the image
    assert "!README.md" in allowed.splitlines()  # pyproject's readme: the build needs it


# -- media over the limits is refused before any work ----------------------------------------------


def _ff(*args: str) -> None:
    import subprocess

    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def bombs(tmp_path_factory) -> dict[str, Path]:
    """Small files that ask for too much (a big frame, a high rate), and allowed ones with
    streams the run never reads: a second (bigger) picture, a subtitle track, two sound
    tracks, and a phone's MOV with a data (timecode) track."""
    d = tmp_path_factory.mktemp("bombs")
    enc = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    _ff("-f", "lavfi", "-i", "testsrc2=s=2560x1440:r=10:d=1", *enc, str(d / "big.mp4"))
    _ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=120:d=1", *enc, str(d / "fast.mp4"))
    _ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=10:d=1",
        "-f", "lavfi", "-i", "testsrc2=s=2560x1440:r=10:d=1",
        "-map", "0:v", "-map", "1:v", *enc, str(d / "two.mp4"))  # fmt: skip
    srt = d / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    _ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=10:d=1", "-i", str(srt),
        "-map", "0:v", "-map", "1:s", *enc, "-c:s", "mov_text", str(d / "subs.mp4"))  # fmt: skip
    _ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=10:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
        "-map", "0:v", "-map", "1:a", "-map", "2:a", *enc, "-c:a", "aac",
        str(d / "two_sounds.mp4"))  # fmt: skip
    _ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-map", "0:v", "-map", "1:a", *enc, "-c:a", "aac", "-timecode", "00:00:00:00",
        str(d / "phone.mov"))  # fmt: skip
    return {p.stem: p for p in d.iterdir() if p.suffix in (".mp4", ".mov")}


@pytest.mark.parametrize(
    "name,says",
    [("big", "2560x1440"), ("fast", "frames a second"),
     ("two", "2560x1440")],  # a second picture is never read, but probing it would decode it
)  # fmt: skip
def test_media_over_the_limits_is_refused_before_any_work(lc, live, bombs, name, says):
    from lull.api import clip

    live.probe, live.transcode = clip.probe, clip.transcode
    live.refusals = RateLimiter(per_client=1, client_window=3600, per_day=10**6)
    trims: list = []
    live.transcode = lambda *a: trims.append(a) or clip.transcode(*a)
    r = post(lc, bombs[name].read_bytes(), name=f"{name}.mp4")
    assert r.status_code == 422, r.text
    assert r.json()["error"] == "unsupported" and says in r.json()["message"]
    assert trims == [] and live.runner.jobs == []
    assert post(lc).status_code == 429  # it counted as a refused upload


@pytest.mark.parametrize("name", ["phone", "subs", "two_sounds"])
def test_streams_the_run_never_reads_are_ignored(lc, live, bombs, name):
    """A phone's data (timecode) track, a subtitle track, a second picture: the run's copy
    maps the first video and the first audio stream only, so they are left out, not refused."""
    from lull.api import clip

    live.probe, live.transcode = clip.probe, clip.transcode
    src = bombs[name]
    assert clip.probe(src).others or len(clip.probe(src).audios) == 2  # they are there
    r = post(lc, src.read_bytes(), name=src.name)
    assert types(parse_sse(r.text))[-1] == "done", r.text
    (job,) = live.runner.jobs
    info = clip.probe(job.video)
    assert info.others == () and len(info.videos) == 1 and info.width == 320


def test_the_runs_copy_is_one_bounded_picture_and_its_first_sound(lc, live, bombs):
    from lull.api import clip

    live.probe, live.transcode = clip.probe, clip.transcode
    r = post(lc, bombs["two_sounds"].read_bytes(), name="two sounds.mov")
    assert types(parse_sse(r.text))[-1] == "done"
    (job,) = live.runner.jobs
    assert job.video.name == "two sounds.mp4"  # an H.264/AAC mp4 whatever came in
    assert sorted(p.name for p in job.video.parent.iterdir()) == ["two sounds.mp4"]
    info = clip.probe(job.video)
    assert (info.video_codec, info.audio_codec, info.audio_streams) == ("h264", "aac", 1)
    assert info.audios == ((1, 48000),) and info.width == 320 and info.fps <= 30
    req = json.loads((job.run_dir / "request.json").read_text("utf-8"))
    assert req["input"] == "input/two sounds.mp4" and req["source"]["name"] == "two sounds.mov"


def _packets(path) -> int:
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    return int(out.stdout.strip())


def test_a_file_whose_headers_understate_its_frame_rate_ends_bounded(lc, live, tmp_path):
    """240 frames a second, but the headers (as far as the API can tell) claim 25: the run's
    copy is cut to the claimed rate, so its work is bounded by the claim and the limits."""
    from dataclasses import replace

    from lull.api import clip

    fast = tmp_path / "fast.mp4"
    _ff("-f", "lavfi", "-i", "testsrc2=s=160x120:r=240:d=2", "-c:v", "libx264", "-preset",
        "ultrafast", "-pix_fmt", "yuv420p", str(fast))  # fmt: skip
    assert _packets(fast) == 480

    def claims_25(path):
        info = clip.probe(path)
        if Path(path).name.startswith(".copy-"):
            return info
        return replace(info, fps=25.0, videos=((160, 120, 25.0),), rate="25/1")

    live.probe, live.transcode = claims_25, clip.transcode
    assert types(parse_sse(post(lc, fast.read_bytes()).text))[-1] == "done"
    (job,) = live.runner.jobs
    assert _packets(job.video) <= 2 * 25 + 1
    assert clip.probe(job.video).fps <= 25


@pytest.mark.parametrize(
    "size,expect", [("1920x1080", (1280, 720)), ("1080x1920", (720, 1280)), ("640x480", (640, 480))]
)
def test_the_runs_copy_fits_720p_and_is_never_upscaled(lc, live, tmp_path, size, expect):
    from lull.api import clip

    src = tmp_path / "in.mp4"
    _ff("-f", "lavfi", "-i", f"testsrc2=s={size}:r=10:d=1", "-c:v", "libx264", "-preset",
        "ultrafast", "-pix_fmt", "yuv420p", str(src))  # fmt: skip
    live.probe, live.transcode = clip.probe, clip.transcode
    assert types(parse_sse(post(lc, src.read_bytes()).text))[-1] == "done"
    (job,) = live.runner.jobs
    info = clip.probe(job.video)
    assert (info.width, info.height) == expect


def test_an_unknown_frame_rate_is_refused(lc, live, bombs):
    from dataclasses import replace

    from lull.api import clip

    live.probe = lambda p: replace(clip.probe(p), videos=((320, 240, 0.0),), fps=0.0, rate="")
    r = post(lc, bombs["two_sounds"].read_bytes())
    assert r.status_code == 422 and r.json()["error"] == "unsupported"
    assert "frame rate" in r.json()["message"] and live.runner.jobs == []


def test_a_copy_that_cannot_be_made_is_refused(lc, live, monkeypatch):
    from lull.api import clip

    def stuck(src, dst, seconds, info):
        raise MediaError("ffmpeg timed out after 180 s")

    live.transcode = stuck
    live.refusals = RateLimiter(per_client=1, client_window=3600, per_day=10**6)
    r = post(lc)
    assert r.status_code == 422 and r.json()["error"] == "unsupported"
    assert "timed out" not in r.json()["message"] and live.runner.jobs == []
    assert post(lc).status_code == 429  # counted: it was the file
    assert clip.TRANSCODE_TIMEOUT <= 300


def test_storing_the_upload_is_not_on_the_visitors_clock(lc, live, monkeypatch):
    from lull.api import live as live_mod

    live.upload_deadline = 0.3
    real = live_mod._save

    def slow(src, dst, limit):
        time.sleep(0.6)  # a slow disk, after the body has arrived
        return real(src, dst, limit)

    monkeypatch.setattr(live_mod, "_save", slow)
    big = b"\0" * (2 << 20)  # spooled to disk: closing the form is a checkpoint
    assert types(parse_sse(post(lc, big).text))[-1] == "done"


def test_an_owner_resume_counts_like_a_start(lc, live, data):
    live.limiter = RateLimiter(per_client=1, client_window=3600, per_day=10**6)
    a = make_unfinished(data, "live-20261027-100000-aaaaaa")
    b = make_unfinished(data, "live-20261027-100000-bbbbbb")
    assert lc.get(f"/v1/runs/{a.name}/events", headers=OWNER_AUTH).status_code == 200
    r = lc.get(f"/v1/runs/{b.name}/events", headers=OWNER_AUTH)
    assert r.status_code == 429 and r.json()["error"] == "rate_limited"
    assert len(live.runner.jobs) == 1


def test_the_limits_in_plain_words():
    from lull.api.clip import Media, over_limits

    def m(**kw):
        base = dict(duration=10.0, width=1920, height=1080, fps=25.0, has_audio=True,
                    videos=((1920, 1080, 25.0),), audios=((2, 48000),), bit_rate=8e6)  # fmt: skip
        return Media(**{**base, **kw})

    assert over_limits(m()) is None
    assert over_limits(m(videos=((1080, 1920, 60000 / 1001),))) is None  # a phone, upright
    # only the first video and the first audio stream count: the copy keeps no other
    assert over_limits(m(audios=((2, 48000),) * 3)) is None
    assert over_limits(m(audios=((2, 48000), (16, 0)))) is None
    assert over_limits(m(videos=((640, 360, 25.0), (7680, 4320, 0.0)))) is None
    assert over_limits(m(others=("data", "subtitle", "attachment"))) is None
    assert "16 channels" in over_limits(m(audios=((16, 48000),)))
    assert "150 Mb/s" in over_limits(m(bit_rate=150e6))
    assert "4096x2160" in over_limits(m(videos=((4096, 2160, 25.0),)))
    # unknown or zero is refused, never waved through
    assert "frame rate" in over_limits(m(videos=((1920, 1080, 0.0),)))
    assert "size" in over_limits(m(videos=((0, 0, 25.0),)))
    assert "sound" in over_limits(m(audios=((0, 48000),)))
    assert "sound" in over_limits(m(audios=((2, 0),)))
    assert "bit rate" in over_limits(m(bit_rate=None))
    assert "bit rate" in over_limits(m(bit_rate=0.0))


def test_the_frame_rate_is_the_higher_of_the_two_stated():
    from lull.api.clip import _rate

    assert _rate({"r_frame_rate": "240/1", "avg_frame_rate": "24/1"}) == (240.0, "240/1")
    assert _rate({"r_frame_rate": "0/0", "avg_frame_rate": "30000/1001"}) == (
        pytest.approx(29.97, abs=0.01), "30000/1001")  # fmt: skip
    assert _rate({}) == (0.0, "")


# -- a run nobody watches stops at its next stage, and its owner restarts it ---------------------


class Watcher:
    """POST /v1/runs over raw ASGI on the test client's own event loop, so the client can go
    away mid-stream: `seen(text)` waits until the stream has carried `text`, `leave()`
    disconnects, `wait()` waits until the app has answered."""

    def __init__(self, lc, content=b"fake video", headers=()) -> None:
        import asyncio

        boundary = "lullboundary"
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.mp4"\r\n'
                "Content-Type: video/mp4\r\n\r\n"
            ).encode()
            + content
            + f"\r\n--{boundary}--\r\n".encode()
        )
        self.text, self.gone = "", threading.Event()
        self._grew = threading.Condition()
        state = {"sent": False}

        async def receive():
            if not state["sent"]:
                state["sent"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            while not self.gone.is_set():
                await asyncio.sleep(0.01)
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                with self._grew:
                    self.text += message.get("body", b"").decode()
                    self._grew.notify_all()

        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/v1/runs",
            "raw_path": b"/v1/runs", "root_path": "", "query_string": b"",
            "headers": [(b"host", b"t"), (b"x-lull-client", b"test"),
                        (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
                        (b"content-length", str(len(body)).encode()), *headers],
            "client": ("testclient", 50000), "server": ("t", 80), "state": {},
        }  # fmt: skip
        self.done = lc.portal.start_task_soon(lc.app, scope, receive, send)

    def seen(self, text: str, timeout: float = 20.0) -> str:
        with self._grew:
            assert self._grew.wait_for(lambda: text in self.text, timeout), self.text
            return self.text

    def leave(self) -> None:
        self.gone.set()

    def wait(self) -> None:
        self.done.result(20)


def staged(job):
    return [
        Event("stage_start", "listen", {}, 1.0),
        Event("stage_end", "listen", {"seconds": 0.1, "cached": False}, 2.0),
        Event("stage_start", "watch", {}, 3.0),
        Event("stage_end", "watch", {"seconds": 0.1, "cached": False}, 4.0),
    ]


def test_a_run_nobody_watches_stops_at_its_next_stage(lc, live, data):
    gate = threading.Event()
    live.runner = Script(staged, gate=gate, gate_at=1)  # in listen, waiting
    w = Watcher(lc)
    run_id = parse_sse(w.seen("event: stage_start"))[0]["run_id"]
    w.leave()
    w.wait()
    # stopped at once: the run is let go and reads stopped (not failed)
    deadline = time.time() + 5
    while live.executor.lock.locked() and time.time() < deadline:
        time.sleep(0.01)
    assert not live.executor.lock.locked() and live.executor.lingers(run_id)
    d = data / "runs" / run_id
    assert lc.get(f"/v1/runs/{run_id}").json()["status"] == "stopped"
    assert not (d / "error.json").exists()
    stored = [json.loads(x) for x in (d / "events.jsonl").read_text("utf-8").splitlines()]
    assert types(stored) == ["run", "stage_start", "stopped"]
    assert stored[-1]["stage"] == "listen" and stored[-1]["message"]
    # while its thread still runs it holds the executor: another start is 409 busy
    stuck = runner = live.runner
    live.runner = Script()
    r = post(lc)
    assert r.status_code == 409 and r.json()["error"] == "busy"
    assert live.executor.busy and lc.get("/v1/health").json()["live_busy"] is True
    # the thread finishes its stage and goes no further: the next stage never starts
    gate.set()
    assert stuck.finished.wait(10)
    (job,) = runner.jobs
    assert job.cancel.is_set() and not job.stop.is_set()
    deadline = time.time() + 5
    while live.executor.busy and time.time() < deadline:
        time.sleep(0.01)
    assert not live.executor.lingers(run_id) and not live.executor.busy
    after = [json.loads(x) for x in (d / "events.jsonl").read_text("utf-8").splitlines()]
    assert after == stored and not (d / "manifest.json").exists()
    # the thread is gone: the next start goes ahead
    assert types(parse_sse(post(lc).text))[-1] == "done"


def test_a_lingering_thread_is_abandoned_at_its_wall_clock(lc, live, data):
    gate = threading.Event()
    live.executor.wall_clock = 1.0
    live.runner = stuck = Script(staged, gate=gate, gate_at=1)
    w = Watcher(lc)
    run_id = parse_sse(w.seen("event: stage_start"))[0]["run_id"]
    w.leave()
    w.wait()
    deadline = time.time() + 5
    while live.executor.busy and time.time() < deadline:
        time.sleep(0.05)
    assert not live.executor.busy and not live.executor.lingers(run_id)  # given up on
    assert not stuck.finished.is_set()  # though it still hangs
    live.runner = Script()
    assert types(parse_sse(post(lc).text))[-1] == "done"
    gate.set()
    assert stuck.finished.wait(10)


class CheckingTF:
    """The engine's FakeTF, checking the ledger before every call as the real client does; it
    notes every attempt, refused or not."""

    attempts: list = []

    def __init__(self, ledger):
        from pipeline_fakes import FakeTF

        self.inner, self.ledger = FakeTF(ledger), ledger

    def json(self, model, messages, schema, *, run_id, stage, role, **kw):
        CheckingTF.attempts.append((stage, role))
        self.ledger.check(run_id, 0.001)
        return self.inner.json(model, messages, schema, run_id=run_id, stage=stage, role=role, **kw)


def test_a_stopped_run_makes_no_model_call_after_the_stop(
    lc, live, data, tmp_path, tone_slides, fast_media, monkeypatch
):
    """The visitor leaves while finalize verifies its first cue: the next verify in that same
    stage is refused before it is made, and the thread ends there."""
    from lull.api import clip
    from lull.api import live as live_mod
    from pipeline_fakes import FakeSpeech

    CheckingTF.attempts = []
    monkeypatch.setattr(live_mod.TokenFactory, "from_settings", staticmethod(CheckingTF))
    monkeypatch.setattr(live_mod.Tavily, "from_settings", staticmethod(lambda ledger: None))
    monkeypatch.setattr(live_mod.NemoSpeechHTTP, "from_settings", staticmethod(FakeSpeech))
    live.probe, live.transcode = clip.probe, clip.transcode
    live.ledger = Ledger(tmp_path / "ledger.db", cap_run=None, cap_day=None, cap_total=None)
    reached, gate, ended = threading.Event(), threading.Event(), threading.Event()

    def runner(job, on_event):
        def hook(ev):
            on_event(ev)
            if ev.type == "call" and ev.data.get("role") == "verify" and not reached.is_set():
                reached.set()
                assert gate.wait(20)

        try:
            live_mod._run_pipeline(job, hook)
        finally:
            ended.set()

    live.runner = runner
    w = Watcher(lc, tone_slides.read_bytes())
    assert reached.wait(60)
    run_id = parse_sse(w.seen("event: run"))[0]["run_id"]
    w.leave()
    w.wait()
    made = len(live.ledger.calls(run_id))
    tried = len(CheckingTF.attempts)
    gate.set()
    assert ended.wait(20)
    # one more model call was tried in that stage (a split or a judge) and refused
    assert [stage for stage, _ in CheckingTF.attempts[tried:]] == ["finalize"]
    assert len(live.ledger.calls(run_id)) == made  # nothing spent after the stop
    assert lc.get(f"/v1/runs/{run_id}").json()["status"] == "stopped"
    deadline = time.time() + 5
    while live.executor.busy and time.time() < deadline:
        time.sleep(0.01)
    assert not live.executor.busy


def test_following_an_unfinished_run_is_its_owners(lc, live, data):
    gate = threading.Event()
    live.runner = Script(gate=gate, gate_at=2)
    with lc.stream("POST", "/v1/runs", files={"file": ("a.mp4", b"x", "video/mp4")}) as p:
        plines = p.iter_lines()
        head = lines_until(plines, "event: call")
        first = json.loads(head[2].split(": ", 1)[1])
        url = f"/v1/runs/{first['run_id']}/events"
        r = lc.get(url)
        assert r.status_code == 403 and r.json()["error"] == "forbidden"
        owner = {"Authorization": f"Bearer {first['edit_token']}"}
        with lc.stream("GET", url, headers=owner) as g:
            assert g.status_code == 200
            glines = g.iter_lines()
            seen = lines_until(glines, "event: call")
            gate.set()
            followed = parse_sse("\n".join(seen + list(glines)))
        list(plines)
    assert types(followed)[-1] == "done"
    assert lc.get(url).status_code == 200  # published: its replay is open to anyone


def test_a_stopped_run_is_replayed_to_its_owner_only(lc, live, data):
    d = make_unfinished(data)
    (d / "error.json").write_text(json.dumps({"type": "MediaError", "message": "bad"}))
    r = lc.get(f"/v1/runs/{d.name}/events")
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
    assert parse_sse(lc.get(f"/v1/runs/{d.name}/events", headers=OWNER_AUTH).text)
    assert live.runner.jobs == []  # failed: replayed, never resumed


def test_an_error_json_wins_over_a_manifest_written_after_the_stop(lc, live, data):
    d = make_unfinished(data)
    (d / "error.json").write_text(json.dumps({"type": "RunTimeout", "message": "too long"}))
    (d / "manifest.json").write_text(json.dumps({"run_id": d.name}))
    assert lc.get(f"/v1/runs/{d.name}").json()["status"] == "failed"
    assert lc.get(f"/v1/runs/{d.name}/original").status_code == 404


def test_the_stopped_runs_ledger_refuses_searches_too(tmp_path):
    from lull.api.live import RunTimeout, StopLedger

    stop = threading.Event()
    inner = Ledger(tmp_path / "l.db", cap_run=None, cap_day=None, cap_total=None)
    ledger = StopLedger(inner, stop)
    ledger.check_searches("r")
    stop.set()
    with pytest.raises(RunTimeout):
        ledger.check_searches("r")


def test_the_owner_restarts_a_stopped_run_from_its_stage_cache(
    lc, live, data, tmp_path, tone_slides, fast_media, monkeypatch
):
    """The real pipeline (the engine's test doubles for the services): the visitor leaves in
    research, the run stops before plan, and the owner's GET restarts it in that request; the
    stages finished before reuse their cache and the run's cap counts both attempts."""
    from lull.api import clip
    from lull.api import live as live_mod
    from pipeline_fakes import FakeSpeech, FakeTF

    monkeypatch.setattr(live_mod.TokenFactory, "from_settings", staticmethod(FakeTF))
    monkeypatch.setattr(live_mod.Tavily, "from_settings", staticmethod(lambda ledger: None))
    monkeypatch.setattr(live_mod.NemoSpeechHTTP, "from_settings", staticmethod(FakeSpeech))
    live.probe, live.transcode = clip.probe, clip.transcode
    live.ledger = Ledger(tmp_path / "ledger.db", cap_run=None, cap_day=None, cap_total=None)
    reached, gate = threading.Event(), threading.Event()

    def runner(job, on_event):
        def hook(ev):
            on_event(ev)
            if ev.type == "stage_start" and ev.stage == "research" and not job.resumed:
                reached.set()
                assert gate.wait(20)

        live_mod._run_pipeline(job, hook)

    live.runner = runner
    w = Watcher(lc, tone_slides.read_bytes())
    assert reached.wait(60)
    first = parse_sse(w.seen('"stage": "research"'), keep_token=True)
    run_id, token = first[0]["run_id"], first[0]["edit_token"]
    w.leave()
    w.wait()
    gate.set()
    spent_before = live.ledger.spent(run_id=run_id)
    assert lc.get(f"/v1/runs/{run_id}").json()["status"] == "stopped"

    r = lc.get(f"/v1/runs/{run_id}/events", headers={"Authorization": f"Bearer {token}"})
    events = parse_sse(r.text)  # what was recorded (up to `stopped`), then the restart
    i = next(i for i, e in enumerate(events) if e["type"] == "run" and e.get("resumed"))
    assert types(events[:i])[-1] == "stopped" and "stage_end" not in types(events[i - 2 : i])
    events = events[i:]
    assert events[-1]["type"] == "done", events[-1]
    ends = {e["stage"]: e["cached"] for e in events if e["type"] == "stage_end"}
    assert ends["listen"] is True and ends["watch"] is True and ends["plan"] is False
    assert lc.get(f"/v1/runs/{run_id}").json()["status"] == "done"
    assert live.ledger.spent(run_id=run_id) >= spent_before > 0


# -- how many may watch at once --------------------------------------------------------------------


def test_at_most_3_follow_a_run_and_8_streams_in_all(lc, live, data):
    import contextlib

    assert (live.max_followers, live.max_streams) == (3, 8)
    gate = threading.Event()
    live.runner = Script(gate=gate, gate_at=2)
    with lc.stream("POST", "/v1/runs", files={"file": ("a.mp4", b"x", "video/mp4")}) as p:
        plines = p.iter_lines()
        head = lines_until(plines, "event: call")
        first = json.loads(head[2].split(": ", 1)[1])
        run_id, owner = first["run_id"], {"Authorization": f"Bearer {first['edit_token']}"}
        url = f"/v1/runs/{run_id}/events"
        with contextlib.ExitStack() as stack:
            follows = [stack.enter_context(lc.stream("GET", url, headers=owner)) for _ in range(3)]
            assert all(g.status_code == 200 for g in follows)
            r = lc.get(url, headers=owner)
            assert r.status_code == 429 and r.json()["error"] == "busy"
            assert int(r.headers["retry-after"]) > 0 and r.json()["message"]
            assert lc.get("/v1/health").status_code == 200  # the rest still answers
        # the three left: room again
        with lc.stream("GET", url, headers=owner) as g:
            assert g.status_code == 200
        gate.set()
        list(plines)
    assert live.streams == 0 and not live.followers


def test_the_streams_in_all_are_capped_replays_included(lc, live, data):
    live.max_streams = 2
    d = data / "showcase" / "demo"
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text(json.dumps({"type": "run", "stage": None, "ts": 1.0, "seq": 0}))
    gate = threading.Event()
    live.runner = Script(gate=gate, gate_at=2)
    with lc.stream("POST", "/v1/runs", files={"file": ("a.mp4", b"x", "video/mp4")}) as p:
        plines = p.iter_lines()
        head = lines_until(plines, "event: call")
        first = json.loads(head[2].split(": ", 1)[1])
        url, owner = (
            f"/v1/runs/{first['run_id']}/events",
            {"Authorization": f"Bearer {first['edit_token']}"},
        )
        with lc.stream("GET", url, headers=owner), lc.stream("GET", url, headers=owner):
            r = lc.get("/v1/runs/demo/events")
            assert r.status_code == 429 and r.json()["error"] == "busy"
        assert lc.get("/v1/runs/demo/events").status_code == 200
        gate.set()
        list(plines)


# -- a custom header on what starts work; one error shape everywhere -------------------------------


def test_starting_a_run_needs_the_client_header(app, live, data):
    with TestClient(app) as c:  # a plain cross-site form post sends no such header
        r = post(c)
        assert r.status_code == 400 and r.json()["error"] == "client_header"
        assert "X-Lull-Client" in r.json()["message"]
        r = c.post("/v1/runs", data={"sample": "ed45"}, headers={"X-Lull-Client": ""})
        assert r.status_code == 400
        assert live.runner.jobs == [] and run_dirs(data) == []
        assert post(c).status_code == 400  # not counted against the visitor either
        r = c.post(
            "/v1/runs",
            files={"file": ("a.mp4", b"x", "video/mp4")},
            headers={"X-Lull-Client": "web"},
        )
        assert r.status_code == 200 and r.headers["cache-control"].startswith("no-store")


@pytest.mark.parametrize(
    "method,path,status,error",
    [
        ("GET", "/v1/runs/r1/events?after=abc", 422, "invalid"),
        ("GET", "/v1/nothing-here", 404, "not_found"),
        ("DELETE", "/v1/runs", 405, "method_not_allowed"),
    ],
)
def test_framework_errors_have_the_error_shape(lc, method, path, status, error):
    r = lc.request(method, path)
    assert r.status_code == status
    body = r.json()
    assert set(body) == {"error", "message"} and body["error"] == error and body["message"]


def _fake_ffprobe(monkeypatch, *, r_rate: str, avg_rate: str, pts: list, duration=10.0):
    """clip.probe's ffprobe answers: the streams (a MediaRecorder WebM's nominal 1000/1, say)
    and, when asked for the first seconds of packets, their times (`pts`, demux order)."""
    import subprocess

    from lull.api import clip

    calls: list = []

    def run(cmd, timeout, **kw):
        calls.append((cmd, timeout))
        if "-read_intervals" in cmd:
            text = "".join(f"{t}\n" for t in pts)
            return subprocess.CompletedProcess(cmd, 0, text.encode(), b"")
        else:
            out = {
                "format": {"duration": str(duration), "format_name": "matroska,webm",
                           "bit_rate": "1000000"},
                "streams": [
                    {"codec_type": "video", "codec_name": "vp8", "width": 640, "height": 480,
                     "r_frame_rate": r_rate, "avg_frame_rate": avg_rate},
                    {"codec_type": "audio", "codec_name": "opus", "channels": 2,
                     "sample_rate": "48000"},
                ],
            }  # fmt: skip
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out).encode(), b"")

    monkeypatch.setattr(clip, "_run", run)
    return calls


def test_an_implausible_stated_frame_rate_is_measured(monkeypatch):
    """A browser recording states 1000 frames a second: the rate that counts is measured from
    the video packets of its first 5 s, (packets - 1) / their time span, never from the
    duration the header states, under the probe's time limit."""
    from lull.api import clip

    times = [i / 30 for i in range(151)]  # 30 a second for 5 s, B-frame order shuffled a bit
    times[3], times[4] = times[4], times[3]
    calls = _fake_ffprobe(monkeypatch, r_rate="1000/1", avg_rate="0/0", pts=times, duration=999)
    info = clip.probe(Path("rec.webm"))
    assert info.fps == pytest.approx(30.0) and info.videos[0][2] == pytest.approx(30.0)
    assert clip.over_limits(info) is None
    read = [c for c in calls if "-read_intervals" in c[0]]
    assert len(read) == 1 and read[0][1] == clip.PROBE_TIMEOUT
    # over the whole window a run may take (the samples' 45 s are within it)
    assert clip.MEASURE_S == 60 and "v:0" in read[0][0] and "%+60" in read[0][0]


@pytest.mark.parametrize("times", [[], [1.0], [2.0, 2.0, 2.0], ["N/A", "N/A"]])
def test_a_rate_that_cannot_be_measured_is_refused(monkeypatch, times):
    from lull.api import clip

    _fake_ffprobe(monkeypatch, r_rate="1000/1", avg_rate="0/0", pts=times)
    assert "could not be measured" in clip.over_limits(clip.probe(Path("rec.webm")))


def test_a_measured_rate_over_the_limit_is_refused(monkeypatch):
    from lull.api import clip

    _fake_ffprobe(monkeypatch, r_rate="1000/1", avg_rate="0/0", pts=[i / 120 for i in range(601)])
    info = clip.probe(Path("rec.webm"))
    assert "120 frames a second" in clip.over_limits(info)


def test_a_plausible_stated_rate_is_not_counted(monkeypatch):
    from lull.api import clip

    calls = _fake_ffprobe(monkeypatch, r_rate="30/1", avg_rate="30/1", pts=[0.0, 0.001])
    assert clip.probe(Path("rec.webm")).fps == 30.0
    assert not any("-read_intervals" in c[0] for c in calls)


# -- refused before the body; the copy carries no timecode -----------------------------------------


def test_a_spent_day_cap_is_402_before_the_body_is_read(app, live, data, tmp_path):
    ledger = Ledger(tmp_path / "spent.db", cap_run=None, cap_day=0.01, cap_total=None)
    ledger.record(
        CallRecord(run_id="earlier", stage="watch", role="watch", provider="tokenfactory",
                   model="m", tokens_in=1, tokens_cached=0, tokens_out=1, latency_s=0.1,
                   cost_usd=0.01, ts=time.time())
    )  # fmt: skip
    live.ledger = ledger
    live.upload_deadline = 5.0
    t0 = time.monotonic()
    status, body = _asgi_post_stalling(app)  # the body never arrives
    assert status == 402 and body["error"] == "budget" and body["scope"] == "day"
    assert time.monotonic() - t0 < 2.0  # not after the upload deadline
    assert live.uploading == 0 and run_dirs(data) == []


def test_the_copy_never_carries_a_timecode_and_probes_cap_the_picture(lc, live, bombs, monkeypatch):
    import subprocess

    from lull.api import clip

    seen: list = []
    real = clip._run

    def run(cmd, timeout, **kw):
        seen.append(cmd)
        return real(cmd, timeout, **kw)

    monkeypatch.setattr(clip, "_run", run)
    live.probe, live.transcode = clip.probe, clip.transcode
    assert types(parse_sse(post(lc, bombs["phone"].read_bytes(), name="phone.mov").text))[-1] == (
        "done"
    )
    (copy,) = [c for c in seen if Path(c[0]).stem == "ffmpeg"]
    assert "-write_tmcd" in copy and copy[copy.index("-write_tmcd") + 1] == "0"
    assert "-map_metadata:s" in copy
    probes = [c for c in seen if Path(c[0]).stem == "ffprobe"]
    assert probes and all("-max_pixels" in c for c in probes)
    (job,) = live.runner.jobs
    tags = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:stream_tags",
         "-of", "json", str(job.video)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    assert "timecode" not in tags and '"data"' not in tags


def test_events_sent_just_before_the_run_ends_all_go_out(lc, live):
    many = [Event("cue", "finalize", {"id": f"c{i:03d}", "status": "verified"}, float(i))
            for i in range(300)]  # fmt: skip
    live.limiter = RateLimiter(per_client=100, client_window=3600, per_day=10**6)
    for _ in range(5):
        live.runner = Script(lambda job: many)
        events = parse_sse(post(lc).text)
        assert types(events).count("cue") == 300 and events[-1]["type"] == "done"


def test_a_copy_with_more_frames_than_its_rate_allows_is_refused(tmp_path, monkeypatch):
    """After the transcode the copy's frames are counted: more than 30 a second of its length
    (plus a little) means the copy is not what it should be, and the upload is refused."""
    import subprocess

    from lull.api import clip

    src = tmp_path / "in.mp4"
    _ff("-f", "lavfi", "-i", "testsrc2=s=160x120:r=10:d=1", "-c:v", "libx264", "-preset",
        "ultrafast", "-pix_fmt", "yuv420p", str(src))  # fmt: skip
    info = clip.probe(src)
    assert clip.transcode(src, tmp_path / "ok.mp4", 60.0, info).is_file()  # honest: passes
    real = clip._run

    def run(cmd, timeout, **kw):
        if "-count_packets" in cmd:
            out = {"streams": [{"nb_read_packets": "500"}], "format": {"duration": "1.0"}}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(out).encode(), b"")
        return real(cmd, timeout, **kw)

    monkeypatch.setattr(clip, "_run", run)
    with pytest.raises(MediaError, match="frames"):
        clip.transcode(src, tmp_path / "bad.mp4", 60.0, info)


def test_the_run_wall_clock_is_15_minutes_and_a_setting(settings, monkeypatch):
    from lull.api.settings import ApiSettings

    assert create_app(settings).state.live.executor.wall_clock == 900.0
    monkeypatch.setenv("LULL_RUN_WALL_CLOCK_S", "1200")
    s = ApiSettings(lull_data=settings.lull_data)
    assert s.lull_run_wall_clock_s == 1200.0
    assert create_app(s).state.live.executor.wall_clock == 1200.0
