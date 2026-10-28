"""Smoke test for a running Lull API: a local container or the Cloud Run service.

    uv run python docker/smoke.py URL [--sample bbb45] [--max-spend 0.05]

1. GET /v1/health answers ok, with the speech server up; GET /v1/samples lists the samples
   and GET /v1/runs answers (an empty showcase is fine).
2. A sample run, left mid-way: POST /v1/runs, read until its first stage starts, then close the
   connection. Locally the run must read `stopped` (not failed). On Cloud Run the platform
   keeps the request open until the run ends (it does not pass a client's leaving on), so the
   run reads `running` and then `done`: with --platform-keeps-requests that is what is checked.
3. A stopped run's owner restarts it: GET /v1/runs/{id}/events with the edit token streams it
   to `done`, the stage finished before the stop taken from the cache, the run's model spend
   (both attempts) within --max-spend. --run-id skips 2 and 3 and uses a finished run.
4. An upload over the size limit (2560x1440, made here with ffmpeg) is refused: 422 unsupported.
5. Over the real data volume: fork the run, approve (or hold) one cue, publish. Publishing
   renames folders, which a Cloud Storage mount takes only with hierarchical namespace.

With --mcp it checks the MCP server at /mcp instead: initialize and tools/list, one
describe_video of the sample to done (progress per stage, model spend within --max-spend), then
get_tracks and get_report of that run. --mcp-list-only stops after tools/list (no spend).

Needs ffmpeg on PATH. Exits non-zero on the first failure. Prints no edit token.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

CLIENT = {"X-Lull-Client": "smoke"}
TIMEOUT = httpx.Timeout(30.0, read=180.0)


def fail(message: str) -> None:
    print(f"FAIL {message}")
    sys.exit(1)


def ok(message: str) -> None:
    print(f"ok   {message}")


def events(lines: Iterator[str]) -> Iterator[dict]:
    """The events of an SSE stream as they arrive (comments and ids skipped)."""
    data: list[str] = []
    for line in lines:
        if line.startswith("data: "):
            data.append(line[6:])
        elif line == "" and data:
            yield json.loads("\n".join(data))
            data = []


def health(c: httpx.Client) -> None:
    t0 = time.monotonic()
    j = c.get("/v1/health").json()
    if not (j.get("ok") and j.get("speech")):
        fail(f"health: {j}")
    b = j["budget"]
    ok(f"health in {time.monotonic() - t0:.1f} s: speech up, spent today ${b['spent_today']:.4f}"
       f" (caps: run ${b['cap_run']}, day ${b['cap_day']}, total ${b['cap_total']})")  # fmt: skip
    samples = [s["id"] for s in c.get("/v1/samples").json()]
    if not samples:
        fail("samples: none listed")
    runs = c.get("/v1/runs")
    if runs.status_code != 200 or not isinstance(runs.json(), list):
        fail(f"runs: {runs.status_code} {runs.text[:200]}")
    ok(f"samples {samples}; recorded runs listed: {len(runs.json())}")


def status_of(c: httpx.Client, run_id: str, owner: dict) -> str | None:
    """The run's status, or None when the answer is not the API's (the platform's own 429)."""
    r = c.get(f"/v1/runs/{run_id}", headers=owner)
    try:
        return r.json()["status"]
    except (ValueError, KeyError):
        return None


def leave_mid_run(c: httpx.Client, sample: str, kept: bool) -> tuple[str, str, str]:
    """Start a sample run and go away once its first stage starts: (run id, edit token,
    the status it settles in)."""
    run_id = token = None
    with c.stream("POST", "/v1/runs", data={"sample": sample}, headers=CLIENT) as r:
        if r.status_code != 200:
            fail(f"start: {r.status_code} {r.read().decode()}")
        for ev in events(r.iter_lines()):
            if ev["type"] == "run":
                run_id, token = ev["run_id"], ev["edit_token"]
            if ev["type"] == "stage_start":
                break  # the page closes
    if not (run_id and token):
        fail("start: no run event")
    owner = {"Authorization": f"Bearer {token}"}
    want = ("done", "failed") if kept else ("stopped",)
    deadline = time.time() + (900 if kept else 60)
    status = None
    while time.time() < deadline:
        status = status_of(c, run_id, owner) or status
        if status in want:
            break
        time.sleep(3)
    if status not in want or status == "failed":
        fail(f"left mid-run: the run reads {status}, not {' or '.join(want)}")
    how = "the platform kept the request open, so it ran to the end" if kept else ""
    ok(f"left mid-run: {run_id} reads {status} {how}".rstrip())
    return run_id, token, status


def restart(c: httpx.Client, run_id: str, token: str, max_spend: float) -> None:
    owner = {"Authorization": f"Bearer {token}"}
    seen: list[dict] = []
    with c.stream("GET", f"/v1/runs/{run_id}/events", headers=owner) as r:
        if r.status_code != 200:
            fail(f"restart: {r.status_code} {r.read().decode()}")
        for ev in events(r.iter_lines()):
            seen.append(ev)
            if ev["type"] in ("done", "error"):
                break
    i = next((i for i, e in enumerate(seen) if e["type"] == "run" and e.get("resumed")), None)
    if i is None:
        fail("restart: no resumed run event")
    rest = seen[i:]
    last = rest[-1]
    if last["type"] != "done":
        fail(f"restart: ended with {last}")
    cached = [e["stage"] for e in rest if e["type"] == "stage_end" and e.get("cached")]
    if not cached:
        fail("restart: no stage came from the cache")
    spend = (last.get("summary") or {}).get("spend") or {}
    total = spend.get("model_usd_all_runs") or spend.get("model_usd") or 0.0
    if total > max_spend:
        fail(f"restart: the run spent ${total:.4f} on models, over ${max_spend}")
    ok(f"restart: done, cached {cached}, model spend ${total:.4f} over both attempts")


def oversize(c: httpx.Client) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clip = Path(tmp) / "big.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             "testsrc2=s=2560x1440:r=10:d=1", "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", str(clip)],
            check=True,
        )  # fmt: skip
        files = {"file": ("big.mp4", clip.read_bytes(), "video/mp4")}
        r = c.post("/v1/runs", files=files, headers=CLIENT)
    if r.status_code != 422 or r.json().get("error") != "unsupported":
        fail(f"oversize: {r.status_code} {r.text}")
    ok(f"oversize: 422 unsupported ({r.json()['message']})")


def publish_over_the_mount(c: httpx.Client, run_id: str) -> None:
    r = c.post(f"/v1/runs/{run_id}/fork", headers=CLIENT)
    if r.status_code != 201:
        fail(f"fork: {r.status_code} {r.text}")
    fork, owner = r.json()["run_id"], {"Authorization": f"Bearer {r.json()['edit_token']}"}
    cues = (c.get(f"/v1/runs/{fork}", headers=owner).json().get("cues") or {}).get("cues") or []
    if not cues:
        fail("fork: the copy has no cues")
    pick = next((q for q in cues if q["status"] in ("verified", "edited")), None)
    if pick is not None:
        r = c.post(f"/v1/runs/{fork}/cues/{pick['id']}/approve", headers=owner)
        what = f"approved {pick['id']}"
    else:
        r = c.post(f"/v1/runs/{fork}/cues/{cues[0]['id']}/hold", headers=owner, json={})
        what = f"held {cues[0]['id']}"
    if r.status_code != 200:
        fail(f"{what}: {r.status_code} {r.text}")
    r = c.post(f"/v1/runs/{fork}/publish", headers=owner)
    if r.status_code != 200 or r.json().get("dirty") is not False:
        fail(f"publish: {r.status_code} {r.text}")
    ok(f"publish over the data volume: fork {fork}, {what}, published")


async def mcp_check(url: str, sample: str, max_spend: float, list_only: bool = False) -> None:
    from mcp import Client

    async with Client(f"{url}/mcp", mode="legacy", read_timeout_seconds=900) as c:
        names = sorted(t.name for t in (await c.list_tools()).tools)
        if names != ["describe_video", "get_report", "get_tracks", "list_samples"]:
            fail(f"mcp tools/list: {names}")
        ok(f"mcp initialize + tools/list: {names}")
        if list_only:
            return
        stages: list[str] = []

        async def progress(p: float, total: float | None, message: str | None) -> None:
            stages.append(message or "")

        r = await c.call_tool("describe_video", {"sample_id": sample}, progress_callback=progress)
        out = r.structured_content or {}
        if r.is_error or out.get("status") != "done":
            fail(f"mcp describe_video: {out or r.content}")
        spend = (out.get("spend") or {}).get("model_usd") or 0.0
        if spend > max_spend:
            fail(f"mcp describe_video spent ${spend:.4f}, over ${max_spend}")
        ok(f"mcp describe_video({sample}): done, {len(stages)} progress notes, "
           f"model spend ${spend:.4f}, cues {out.get('cues')}")  # fmt: skip
        run_id = out["run_id"]
        tracks = (await c.call_tool("get_tracks", {"run_id": run_id})).structured_content or {}
        report = (await c.call_tool("get_report", {"run_id": run_id})).structured_content or {}
        if not tracks.get("cues") or not report.get("wcag"):
            fail("mcp get_tracks / get_report came back empty")
        rows = {w["criterion"]: w["status"] for w in report["wcag"]}
        ok(f"mcp get_tracks: {len(tracks['cues'])} cues, files {sorted(tracks['files'])}")
        ok(f"mcp get_report: {rows}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("--sample", default="bbb45")
    ap.add_argument("--max-spend", type=float, default=0.05)
    ap.add_argument("--mcp", action="store_true", help="check the MCP server at /mcp instead")
    ap.add_argument("--mcp-list-only", action="store_true", help="with --mcp: no describe")
    ap.add_argument("--run-id", help="use this finished run instead of starting one")
    ap.add_argument(
        "--platform-keeps-requests",
        action="store_true",
        help="Cloud Run: leaving does not stop the run; expect it to finish",
    )
    args = ap.parse_args()
    if args.mcp:
        with httpx.Client(base_url=args.url.rstrip("/"), timeout=TIMEOUT) as c:
            health(c)
        asyncio.run(
            mcp_check(args.url.rstrip("/"), args.sample, args.max_spend, args.mcp_list_only)
        )
        print("mcp smoke passed")
        return
    with httpx.Client(base_url=args.url.rstrip("/"), timeout=TIMEOUT) as c:
        health(c)
        if args.run_id:
            run_id = args.run_id
        else:
            run_id, token, status = leave_mid_run(c, args.sample, args.platform_keeps_requests)
            if status == "stopped":
                restart(c, run_id, token, args.max_spend)
        oversize(c)
        publish_over_the_mount(c, run_id)
    print("smoke passed")


if __name__ == "__main__":
    main()
