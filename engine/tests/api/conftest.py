from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lull.api.app import create_app
from lull.api.settings import ApiSettings

FILES = {
    "descriptions.vtt": b"WEBVTT\n\n00:00.000 --> 00:01.000\nhi\n",
    "described-audio.m4a": bytes(range(256)) * 8,
    "described.mp4": bytes(range(256)) * 16,
    "report.html": b"<html><body>report</body></html>",
    "keyframes/c001.jpg": b"\xff\xd8\xff fake",
}


def _make_run(
    root: Path,
    run_id: str,
    *,
    title="clip.mp4",
    created="2026-10-27T10:00:00+00:00",
    manifest=True,
    cost=0.03,
    failed=False,
) -> Path:
    d = root / run_id
    d.mkdir(parents=True)
    outputs = {}
    for rel, data in FILES.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        outputs[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    (d / "secret.txt").write_text("nope")
    (d / "run.log").write_text("log")
    if failed:
        (d / "error.json").write_text("{}")
        return d
    if manifest:
        (d / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "created": created,
                    "video": {"name": title, "sha256": "x", "duration": 90.0},
                    "outputs": outputs,
                    "summary": {
                        "cues": {
                            "verified": 5,
                            "edited": 0,
                            "approved": 0,
                            "held": 0,
                            "extended": 2,
                        },
                        "cost_usd": cost,
                    },
                }
            )
        )
        (d / "cues.json").write_text(json.dumps({"run_id": run_id, "cues": [], "extended": []}))
    return d


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def settings(data, tmp_path):
    return ApiSettings(lull_data=data, lull_web_origins="http://web.test")


@pytest.fixture
def client(settings, monkeypatch, tmp_path):
    monkeypatch.setenv("LULL_HOME", str(tmp_path / "home"))
    from lull.config import get_settings

    get_settings.cache_clear()
    yield TestClient(create_app(settings))
    get_settings.cache_clear()


@pytest.fixture
def make_run():
    return _make_run
