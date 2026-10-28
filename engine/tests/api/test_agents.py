"""MCP: the hosted server (/mcp on the API: samples and run ids only) and the local one (`lull
mcp`, stdio: a file path too, and the review queue), driven by an in-process MCP client. The
engine is a scripted runner unless a test runs the real pipeline with the engine's fakes."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from test_live import CLIENT, Script, fake_probe, fake_transcode, staged

from lull.api.app import create_app
from lull.api.ratelimit import RateLimiter
from lull.ledger import Ledger
from lull.pipeline import Event

HOSTED_TOOLS = {"list_samples", "describe_video", "get_tracks", "get_report"}
LOCAL_TOOLS = HOSTED_TOOLS | {"review_queue"}

SAMPLES_JSON = {
    "samples": [
        {"id": "street", "file": "street.mp4", "title": "Street (excerpt)", "credit": "Someone",
         "licence": "CC BY 3.0", "duration": 10.0},
    ]
}  # fmt: skip


@pytest.fixture
def app(settings, monkeypatch, tmp_path, data):
    monkeypatch.setenv("LULL_HOME", str(tmp_path / "home"))
    from lull.config import get_settings

    get_settings.cache_clear()
    (data / "samples").mkdir(parents=True, exist_ok=True)
    (data / "samples" / "street.mp4").write_bytes(b"DUR=10;")
    (data / "samples" / "samples.json").write_text(json.dumps(SAMPLES_JSON))
    app = create_app(settings)
    live = app.state.live
    live.probe, live.transcode, live.runner = fake_probe, fake_transcode, Script()
    live.envelope = None
    agents = app.state.agents
    agents.real_visitor = agents.visitor
    agents.visitor = lambda ctx: "visitor-test"  # in-process there is no address to key on
    yield app
    get_settings.cache_clear()


@pytest.fixture
def live(app):
    return app.state.live


def call(server, tool: str, args: dict | None = None, progress: list | None = None):
    """One tool call through an in-process MCP client."""

    async def go():
        async def on_progress(p, total, message):
            if progress is not None:
                progress.append((p, total, message))

        async with Client(server) as c:
            return await c.call_tool(tool, args or {}, progress_callback=on_progress)

    return asyncio.run(go())


def text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


# -- the hosted server -----------------------------------------------------------------------------


def test_the_hosted_tools_and_their_schemas(app):
    async def go():
        async with Client(app.state.agents) as c:
            return (await c.list_tools()).tools

    tools = {t.name: t for t in asyncio.run(go())}
    assert set(tools) == HOSTED_TOOLS
    for t in tools.values():
        assert t.description and len(t.description) > 40 and t.output_schema
    props = tools["describe_video"].input_schema["properties"]
    assert set(props) == {"sample_id", "mode"}  # no url, no path, no upload
    assert props["mode"]["enum"] == ["standard", "extended"]


def test_list_samples(app):
    r = call(app.state.agents, "list_samples")
    assert not r.is_error
    (s,) = r.structured_content["samples"]
    assert s == {"id": "street", "title": "Street (excerpt)", "duration": 10.0,
                 "credit": "Someone", "licence": "CC BY 3.0"}  # fmt: skip


def test_describe_a_sample_with_progress_and_an_edit_token(app, live, data, caplog):
    caplog.set_level(logging.DEBUG)
    live.runner = Script(staged)
    progress: list = []
    r = call(app.state.agents, "describe_video", {"sample_id": "street"}, progress)
    assert not r.is_error, text(r)
    out = r.structured_content
    assert out["status"] == "done" and out["run_id"].startswith("live-")
    assert len(out["edit_token"]) >= 40
    assert [m for _, _, m in progress if m.startswith("listen")] and progress[-1][1] == 7
    assert all(p <= total for p, total, _ in progress)
    req = json.loads((data / "runs" / out["run_id"] / "request.json").read_text("utf-8"))
    assert req["source"] == {"kind": "sample", "id": "street", "name": "street.mp4"}
    assert out["edit_token"] not in caplog.text  # never logged
    (job,) = live.runner.jobs
    assert job.opts.mode == "standard"


@pytest.mark.parametrize(
    "args",
    [
        {"sample_id": "street", "path": "/etc/passwd"},
        {"sample_id": "street", "url": "http://169.254.169.254/computeMetadata/v1/"},
        {"sample_id": "street", "file": "x"},
    ],
)
def test_the_hosted_server_takes_no_url_path_or_upload(app, live, args):
    r = call(app.state.agents, "describe_video", args)
    assert r.is_error and "unsupported" in text(r)
    assert live.runner.jobs == []


@pytest.mark.parametrize(
    "sample_id", ["http://169.254.169.254/", "../../etc/passwd", "/data/runs/x", "nope"]
)
def test_only_known_samples_are_described(app, live, sample_id):
    r = call(app.state.agents, "describe_video", {"sample_id": sample_id})
    assert r.is_error and "sample" in text(r)
    assert live.runner.jobs == []


def test_a_second_describe_while_busy_is_a_busy_error(app, live):
    gate = threading.Event()
    live.runner = Script(gate=gate, gate_at=1)

    async def go():
        async with Client(app.state.agents) as c:
            first = asyncio.create_task(c.call_tool("describe_video", {"sample_id": "street"}))
            for _ in range(200):
                if live.executor.busy:
                    break
                await asyncio.sleep(0.01)
            second = await c.call_tool("describe_video", {"sample_id": "street"})
            gate.set()
            return await first, second

    first, second = asyncio.run(go())
    assert second.is_error and "busy" in text(second)
    assert not first.is_error and first.structured_content["status"] == "done"


def test_refusals_carry_the_apis_codes(app, live, tmp_path):
    live.ledger = Ledger(tmp_path / "caps.db", cap_run=None, cap_day=0, cap_total=None)
    r = call(app.state.agents, "describe_video", {"sample_id": "street"})
    assert r.is_error and "budget" in text(r)
    live.ledger = Ledger(tmp_path / "free.db", cap_run=None, cap_day=None, cap_total=None)
    live.limiter = RateLimiter(per_client=1, client_window=3600, per_day=1000)
    assert not call(app.state.agents, "describe_video", {"sample_id": "street"}).is_error
    r = call(app.state.agents, "describe_video", {"sample_id": "street"})
    assert r.is_error and "rate_limited" in text(r)


def test_a_failed_run_is_reported_failed(app, live):
    live.runner = Script(
        lambda job: [Event("stage_start", "listen", {}, 1.0)],
        exc=lambda job: RuntimeError("the speech server is down"),
        manifest=False,
    )
    r = call(app.state.agents, "describe_video", {"sample_id": "street"})
    out = r.structured_content
    assert out["status"] == "failed" and "speech server" in out["error"]


def test_tracks_and_report_of_a_run(app, data, make_run):
    d = make_run(data / "runs", "r1")
    r = call(app.state.agents, "get_tracks", {"run_id": "r1"})
    assert not r.is_error, text(r)
    out = r.structured_content
    assert out["run_id"] == "r1" and isinstance(out["cues"], list)
    for name, url in out["files"].items():
        assert url.endswith(f"/v1/runs/r1/files/{name}")
    r = call(app.state.agents, "get_report", {"run_id": "r1"})
    assert not r.is_error, text(r)
    assert r.structured_content["run_id"] == "r1"
    assert d.is_dir()


def test_unfinished_and_unknown_runs(app, data):
    d = data / "runs" / "live-20261028-unfinished"
    (d / "input").mkdir(parents=True)
    for tool in ("get_tracks", "get_report"):
        r = call(app.state.agents, tool, {"run_id": d.name})
        assert r.is_error and "not_ready" in text(r)
        r = call(app.state.agents, tool, {"run_id": "nope"})
        assert r.is_error and "not_found" in text(r)


def test_a_real_sample_run_end_to_end(app, live, data, tmp_path, tone_slides, fast_media,
                                      monkeypatch):  # fmt: skip
    """The real pipeline (the engine's fakes for the services) on a sample, through MCP:
    describe, then the tracks and the report the manifest gives."""
    from lull.api import clip
    from lull.api import live as live_mod
    from pipeline_fakes import FakeSpeech, FakeTF

    shutil.copyfile(tone_slides, data / "samples" / "street.mp4")
    monkeypatch.setattr(live_mod.TokenFactory, "from_settings", staticmethod(FakeTF))
    monkeypatch.setattr(live_mod.Tavily, "from_settings", staticmethod(lambda ledger: None))
    monkeypatch.setattr(live_mod.NemoSpeechHTTP, "from_settings", staticmethod(FakeSpeech))
    live.probe, live.transcode, live.runner = clip.probe, clip.transcode, live_mod._run_pipeline
    live.max_sample_seconds = 45.0
    live.ledger = Ledger(tmp_path / "ledger.db", cap_run=None, cap_day=None, cap_total=None)
    progress: list = []
    r = call(app.state.agents, "describe_video", {"sample_id": "street"}, progress)
    assert not r.is_error, text(r)
    out = r.structured_content
    assert out["status"] == "done" and out["cues"]["verified"] >= 1
    assert {w["criterion"] for w in out["wcag"]} == {"1.2.3", "1.2.5", "1.2.7"}
    assert out["spend"]["model_usd"] is not None
    stages = [m.split(":")[0] for _, _, m in progress]
    assert "publish" in stages and progress[-1][0] == 7
    tracks = call(app.state.agents, "get_tracks", {"run_id": out["run_id"]}).structured_content
    assert tracks["cues"] and all("-->" in c["timecode"] for c in tracks["cues"])
    assert {"descriptions.vtt", "described.mp4"} <= set(tracks["files"])
    report = call(app.state.agents, "get_report", {"run_id": out["run_id"]}).structured_content
    assert [w["criterion"] for w in report["wcag"]] == ["1.2.3", "1.2.5", "1.2.7"]
    assert all(isinstance(w["blockers"], list) for w in report["wcag"])


# -- over HTTP: /mcp on the API --------------------------------------------------------------------


def test_mcp_answers_on_the_api_over_streamable_http(app, live):
    """initialize and tools/list over HTTP at /mcp, and a describe whose limits are keyed to
    the visitor's address like the REST API's."""
    app.state.agents.visitor = app.state.agents.real_visitor
    app.state.settings.lull_public_url = "http://api.test"  # the host /mcp answers to
    live.limiter = RateLimiter(per_client=1, client_window=3600, per_day=1000)
    with TestClient(app, headers=CLIENT) as c:

        async def go():
            http = httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app, client=("203.0.113.9", 1234)),
                base_url="http://api.test",
            )
            transport = streamable_http_client("http://api.test/mcp", http_client=http)
            async with Client(transport, mode="legacy") as mc:
                names = {t.name for t in (await mc.list_tools()).tools}
                first = await mc.call_tool("describe_video", {"sample_id": "street"})
                second = await mc.call_tool("describe_video", {"sample_id": "street"})
            await http.aclose()
            return names, first, second

        names, first, second = c.portal.call(go)
        assert names == HOSTED_TOOLS
        assert not first.is_error and first.structured_content["status"] == "done"
        assert second.is_error and "rate_limited" in text(second)
        # the REST API from another address is not held back by it
        r = c.post("/v1/runs", data={"sample": "street"})
        assert r.status_code == 200


# -- the local server ----------------------------------------------------------------------------


@pytest.fixture
def local(tmp_path, monkeypatch):
    from lull.api.agents import local_server

    home = tmp_path / "home"
    monkeypatch.setenv("LULL_HOME", str(home))
    from lull.config import get_settings

    get_settings.cache_clear()
    server = local_server(home)
    server.lull.runner = Script(staged)
    server.lull.probe = fake_probe
    yield server
    get_settings.cache_clear()


def test_the_local_tools(local):
    async def go():
        async with Client(local) as c:
            return (await c.list_tools()).tools

    tools = {t.name: t for t in asyncio.run(go())}
    assert set(tools) == LOCAL_TOOLS
    assert "path" in tools["describe_video"].input_schema["properties"]


def test_describe_a_local_file_under_lull_home(local, tmp_path):
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"DUR=12;")
    progress: list = []
    r = call(local, "describe_video", {"path": str(video)}, progress)
    assert not r.is_error, text(r)
    out = r.structured_content
    assert out["status"] == "done" and out["edit_token"] is None
    (job,) = local.lull.runner.jobs
    assert job.video == video.resolve() and job.run_dir.parent == tmp_path / "home" / "runs"
    assert progress


@pytest.mark.parametrize("how", ["missing", "folder", "extension", "both", "neither"])
def test_local_paths_are_checked(local, tmp_path, how):
    folder = tmp_path / "clips.mp4"
    folder.mkdir()
    (tmp_path / "notes.txt").write_text("hi")
    args = {
        "missing": {"path": str(tmp_path / "nope.mp4")},
        "folder": {"path": str(folder)},
        "extension": {"path": str(tmp_path / "notes.txt")},
        "both": {"path": str(tmp_path / "notes.txt"), "sample_id": "x"},
        "neither": {},
    }[how]
    r = call(local, "describe_video", args)
    assert r.is_error and local.lull.runner.jobs == []


def test_the_review_queue_lists_runs_with_held_lines_newest_first(local, tmp_path):
    runs = tmp_path / "home" / "runs"
    for rid, created, held in (("a", "2026-10-01T00:00:00+00:00", 1),
                               ("b", "2026-10-03T00:00:00+00:00", 2),
                               ("c", "2026-10-02T00:00:00+00:00", 0)):  # fmt: skip
        d = runs / rid
        d.mkdir(parents=True)
        summary = {"held": [{"id": f"c{i}", "reason": "not in the frames"} for i in range(held)]}
        (d / "manifest.json").write_text(json.dumps(
            {"run_id": rid, "created": created, "video": {"name": f"{rid}.mp4"},
             "summary": summary}))  # fmt: skip
        cues = [{"id": f"c{i}", "text": f"line {i}", "status": "held", "start": 1.0,
                 "audio_dur": None} for i in range(held)]  # fmt: skip
        (d / "cues.json").write_text(json.dumps({"cues": cues, "extended": []}))
    r = call(local, "review_queue")
    assert not r.is_error, text(r)
    items = r.structured_content["runs"]
    assert [i["run_id"] for i in items] == ["b", "a"]
    assert items[0]["held"][0] == {"id": "c0", "text": "line 0", "reason": "not in the frames"}


def test_local_tracks_give_paths(local, tmp_path, make_run):
    d = make_run(tmp_path / "home" / "runs", "r1")
    out = call(local, "get_tracks", {"run_id": "r1"}).structured_content
    for name, path in out["files"].items():
        assert Path(path) == d / name and Path(path).is_file()


def test_the_cli_serves_mcp():
    from typer.testing import CliRunner

    from lull.cli import app as cli

    r = CliRunner().invoke(cli, ["mcp", "--help"])
    assert r.exit_code == 0 and "stdio" in r.output.lower()


def test_lull_mcp_speaks_mcp_over_stdio(tmp_path):
    """The real `lull mcp` process: nothing but protocol on its stdout."""
    import sys

    from mcp.client.stdio import StdioServerParameters

    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from lull.cli import app; app()", "mcp"],
        env={"LULL_HOME": str(tmp_path / "home"), "LULL_DATA": str(tmp_path / "data")},
    )

    async def go():
        async with Client(params, mode="legacy") as c:
            return {t.name for t in (await c.list_tools()).tools}

    assert asyncio.run(go()) == LOCAL_TOOLS


# -- a sample start passes the same gates as POST /v1/runs ----------------------------------------


def _start(live, key):
    from lull.api.live import start_sample
    from lull.types import RunOptions

    return start_sample(live, key, "street", RunOptions())


async def _to_the_end(started) -> None:
    while (await started.queue.get()) is not None:
        pass


def test_concurrent_sample_starts_take_the_prepare_slots(app, live):
    from lull.api.live import LiveError

    entered, gate = threading.Event(), threading.Event()

    def slow(src, dst, seconds, info):
        entered.set()
        assert gate.wait(10)
        return fake_transcode(src, dst, seconds)

    live.transcode = slow

    async def go():
        a = asyncio.create_task(_start(live, "visitor-a"))
        while not entered.is_set():
            await asyncio.sleep(0.01)
        try:
            await _start(live, "visitor-a")  # the same visitor, its first still preparing
        except LiveError as e:
            same = e
        b = asyncio.create_task(_start(live, "visitor-b"))
        await asyncio.sleep(0.2)
        assert live.uploading == 2
        gate.set()
        results = await asyncio.gather(a, b, return_exceptions=True)
        for r in results:
            if not isinstance(r, BaseException):
                await _to_the_end(r)
        return same, results

    same, results = asyncio.run(go())
    assert same.error == "one_upload_at_a_time"
    started = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, LiveError)]
    assert len(started) == 1 and len(refused) == 1 and refused[0].error == "busy"
    assert live.uploading == 0 and not live.upload_keys
    assert len(live.runner.jobs) == 1


def test_sample_starts_are_bounded_by_the_slots_and_refusals(app, live):
    from lull.api.live import LiveError

    entered, gate = threading.Event(), threading.Event()

    def slow(src, dst, seconds, info):
        entered.set()
        assert gate.wait(10)
        return fake_transcode(src, dst, seconds)

    live.transcode, live.max_uploads = slow, 1

    async def go():
        a = asyncio.create_task(_start(live, "visitor-a"))
        while not entered.is_set():
            await asyncio.sleep(0.01)
        try:
            await _start(live, "visitor-b")
        except LiveError as e:
            full = e
        gate.set()
        await _to_the_end(await a)
        return full

    assert asyncio.run(go()).error == "uploads_busy"
    live.refusals = RateLimiter(per_client=1, client_window=3600, per_day=10**6)
    live.refusals.hit("visitor-test")
    r = call(app.state.agents, "describe_video", {"sample_id": "street"})
    assert r.is_error and "rate_limited" in text(r)


# -- leaving a describe over real HTTP stops the run -----------------------------------------------


@pytest.fixture
def served(app):
    """The app on a real uvicorn server (a thread), so a client can drop its connection."""
    import socket
    import time

    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(20)


def _stopped(app, live, timeout=10.0) -> str:
    import time

    from lull.api import facts

    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = [d for d in (app.state.settings.runs_dir).iterdir() if d.is_dir()]
        if runs and not live.executor.lock.locked():
            return facts.run_status(runs[0], live.executor.get(runs[0].name) is not None)
        time.sleep(0.05)
    return "still running"


def _wait_busy(live) -> None:
    import time

    deadline = time.time() + 20
    while not live.executor.busy and time.time() < deadline:
        time.sleep(0.02)
    assert live.executor.busy


@pytest.mark.parametrize("mode", ["legacy", "auto"])
def test_a_client_that_cancels_a_describe_stops_the_run(app, live, served, mode):
    app.state.agents.visitor = app.state.agents.real_visitor
    gate = threading.Event()
    live.runner = Script(staged, gate=gate, gate_at=1)

    async def go():
        async with Client(f"{served}/mcp", mode=mode) as c:
            task = asyncio.create_task(c.call_tool("describe_video", {"sample_id": "street"}))
            await asyncio.to_thread(_wait_busy, live)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(go())
    try:
        assert _stopped(app, live) == "stopped"
    finally:
        gate.set()


def test_a_dropped_connection_stops_the_run(app, live, served):
    """No MCP cancel at all: the HTTP connection just goes away mid-run."""
    import httpx

    app.state.agents.visitor = app.state.agents.real_visitor
    gate = threading.Event()
    live.runner = Script(staged, gate=gate, gate_at=1)
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "describe_video", "arguments": {"sample_id": "street"},
                       "_meta": {"progressToken": "p1"}}}  # fmt: skip
    headers = {"Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-06-18"}  # fmt: skip
    with httpx.Client(timeout=30) as c:
        with c.stream("POST", f"{served}/mcp", json=call, headers=headers) as r:
            assert r.status_code == 200, r.read()
            _wait_busy(live)
            for line in r.iter_lines():
                if "progress" in line:
                    break  # the first progress note arrived: now leave
    try:
        assert _stopped(app, live) == "stopped"
    finally:
        gate.set()


# -- who may call /mcp -----------------------------------------------------------------------------

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "1"}}}  # fmt: skip
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def _post_mcp(app, *, public="http://api.test", **headers):
    app.state.settings.lull_public_url = public
    with TestClient(app, base_url="http://api.test") as c:
        return c.post("/mcp", json=INIT, headers={**MCP_HEADERS, **headers})


def test_an_origin_must_be_one_of_the_sites(app):
    r = _post_mcp(app, Origin="http://evil.test")
    assert r.status_code == 403 and r.json()["error"] == "origin" and r.json()["message"]
    assert _post_mcp(app, Origin="http://web.test").status_code == 200  # LULL_WEB_ORIGINS
    assert _post_mcp(app).status_code == 200  # no Origin: not a browser


def test_mcp_answers_its_own_host_only(app):
    assert _post_mcp(app, Host="evil.test").status_code == 421
    r = _post_mcp(app, Host="evil.test")
    assert r.json()["error"] == "host"
    assert _post_mcp(app, public="").status_code == 421  # no LULL_PUBLIC_URL: localhost only


def test_a_large_mcp_body_is_413(app):
    app.state.settings.lull_public_url = "http://api.test"
    big = {**INIT, "pad": "x" * 70_000}
    with TestClient(app, base_url="http://api.test") as c:
        r = c.post("/mcp", json=big, headers=MCP_HEADERS)
    assert r.status_code == 413 and r.json()["error"] == "too_large"


def test_links_come_from_lull_public_url_never_from_the_host(app, data, make_run):
    make_run(data / "runs", "r1")
    agents = app.state.agents
    app.state.settings.lull_public_url = ""
    files = call(agents, "get_tracks", {"run_id": "r1"}).structured_content["files"]
    assert files["descriptions.vtt"] == "/v1/runs/r1/files/descriptions.vtt"
    app.state.settings.lull_public_url = "https://lull.example,https://smoke---lull.example"
    files = call(agents, "get_tracks", {"run_id": "r1"}).structured_content["files"]
    assert files["descriptions.vtt"] == "https://lull.example/v1/runs/r1/files/descriptions.vtt"


def test_startup_warns_without_lull_public_url(app, caplog):
    app.state.settings.lull_public_url = ""
    with caplog.at_level(logging.WARNING), TestClient(app):
        pass
    assert "LULL_PUBLIC_URL" in caplog.text


def test_a_caller_with_no_address_is_refused_not_pooled(app, live):
    app.state.agents.visitor = app.state.agents.real_visitor  # in-process: no address
    r = call(app.state.agents, "describe_video", {"sample_id": "street"})
    assert r.is_error and "unknown_client" in text(r) and live.runner.jobs == []


def test_local_errors_are_scrubbed_like_the_apis(local, tmp_path):
    video = tmp_path / "secret-folder" / "talk.mp4"
    video.parent.mkdir()
    video.write_bytes(b"DUR=12;")
    local.lull.runner = Script(
        staged, exc=lambda job: RuntimeError(f"ffmpeg failed on {job.video}"), manifest=False
    )
    out = call(local, "describe_video", {"path": str(video)}).structured_content
    assert out["status"] == "failed" and "talk.mp4" in out["error"]
    assert str(tmp_path) not in out["error"] and "secret-folder" not in out["error"]


def test_describe_says_it_blocks_and_streams_progress(app, local):
    async def go(server):
        async with Client(server) as c:
            return {t.name: t for t in (await c.list_tools()).tools}["describe_video"]

    for server in (app.state.agents, local):
        d = asyncio.run(go(server)).description
        assert "progress" in d and "5 minutes" in d
