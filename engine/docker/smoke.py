"""Smoke test for a running Lull API: a local container or the Cloud Run service.

    uv run python docker/smoke.py URL [--sample bbb45] [--max-spend 0.05]

1. GET /v1/health answers ok, with the speech server up.
2. A sample run, left mid-way: POST /v1/runs, read until its first stage starts, then close the
   connection. The run must read `stopped` (not failed).
3. Its owner restarts it: GET /v1/runs/{id}/events with the edit token streams it to `done`,
   the stage finished before the stop taken from the cache, the run's model spend (both
   attempts) within --max-spend.
4. An upload over the size limit (2560x1440, made here with ffmpeg) is refused: 422 unsupported.
5. Over the real data volume: fork the run, approve (or hold) one cue, publish. Publishing
   renames folders, which a Cloud Storage mount takes only with hierarchical namespace.

Needs ffmpeg on PATH. Exits non-zero on the first failure. Prints no edit token.
"""

from __future__ import annotations

import argparse
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
    j = c.get("/v1/health").json()
    if not (j.get("ok") and j.get("speech")):
        fail(f"health: {j}")
    ok(f"health: speech up, spent today ${j['budget']['spent_today']}")


def leave_mid_run(c: httpx.Client, sample: str) -> tuple[str, str]:
    """Start a sample run and go away once its first stage starts: (run id, edit token)."""
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
    deadline = time.time() + 60
    status = None
    while time.time() < deadline:
        status = c.get(f"/v1/runs/{run_id}", headers=owner).json()["status"]
        if status == "stopped":
            break
        time.sleep(0.5)
    if status != "stopped":
        fail(f"left mid-run: the run reads {status}, not stopped")
    ok(f"left mid-run: {run_id} reads stopped")
    return run_id, token


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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("--sample", default="bbb45")
    ap.add_argument("--max-spend", type=float, default=0.05)
    args = ap.parse_args()
    with httpx.Client(base_url=args.url.rstrip("/"), timeout=TIMEOUT) as c:
        health(c)
        run_id, token = leave_mid_run(c, args.sample)
        restart(c, run_id, token, args.max_spend)
        oversize(c)
        publish_over_the_mount(c, run_id)
    print("smoke passed")


if __name__ == "__main__":
    main()
