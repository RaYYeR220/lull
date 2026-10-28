from __future__ import annotations

import pytest

from lull.api.settings import ApiSettings


def test_settings_origins_split():
    s = ApiSettings(lull_web_origins="http://a.test, http://b.test")
    assert s.origins == ["http://a.test", "http://b.test"]


def test_health_shape(client, monkeypatch):
    import lull.speech

    monkeypatch.setattr(lull.speech.NemoSpeechHTTP, "health", lambda self: True)
    r = client.get("/v1/health")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["speech"] is True
    assert set(j["budget"]) == {
        "spent_today",
        "cap_day",
        "spent_total",
        "cap_total",
        "cap_run",
        "searches_today",
        "searches_cap_run",
        "searches_cap_day",
    }


def test_health_speech_error_is_false(client, monkeypatch):
    import lull.speech

    def boom(self):
        raise RuntimeError("down")

    monkeypatch.setattr(lull.speech.NemoSpeechHTTP, "health", boom)
    assert client.get("/v1/health").json()["speech"] is False


def test_cors_allowed_and_denied(client):
    ok = client.get("/v1/runs", headers={"Origin": "http://web.test"})
    assert ok.headers["access-control-allow-origin"] == "http://web.test"
    bad = client.get("/v1/runs", headers={"Origin": "http://evil.test"})
    assert "access-control-allow-origin" not in bad.headers


def test_list_runs_order(client, data, make_run):
    make_run(data / "showcase", "show1", title="Showcase")
    make_run(data / "showcase", "show0", created="2026-10-01T00:00:00+00:00")
    make_run(data / "showcase", "bad", failed=True)
    make_run(data / "showcase", "pending", manifest=False)
    make_run(data / "runs", "new", created="2026-10-02T00:00:00+00:00")  # a sample's live run
    items = client.get("/v1/runs").json()
    pub = [i["id"] for i in items if i["status"] == "done"]
    assert pub == ["show0", "show1"]
    assert {i["kind"] for i in items} == {"showcase"}
    first = next(i for i in items if i["id"] == "show1")
    assert first["title"] == "Showcase" and first["duration"] == 90.0
    assert first["status"] == "done" and first["cost_usd"] == 0.03
    assert first["cues"]["verified"] == 5
    by_id = {i["id"]: i for i in items}
    assert by_id["bad"]["status"] == "failed"
    assert by_id["pending"]["status"] == "stopped"  # not being described here


def test_the_list_is_the_recorded_runs_only(client, data, make_run):
    """The official sample runs (the showcase) and nothing else: visitors' runs of the samples,
    forks and uploads are opened by their id only."""
    make_run(data / "showcase", "ed45")
    for i in range(5):
        make_run(data / "runs", f"r{i:02d}", created=f"2026-10-01T00:{i:02d}:00+00:00")
    make_run(data / "runs", "up", source="upload")
    assert [i["id"] for i in client.get("/v1/runs").json()] == ["ed45"]
    assert client.get("/v1/runs/r00").json()["status"] == "done"


def test_get_run_merged(client, data, make_run):
    make_run(data / "runs", "r1")
    j = client.get("/v1/runs/r1").json()
    assert j["manifest"]["run_id"] == "r1" and j["cues"]["cues"] == []


def test_get_showcase_run(client, data, make_run):
    make_run(data / "showcase", "s1")
    assert client.get("/v1/runs/s1").status_code == 200


@pytest.mark.parametrize("rid", ["nope", "bad%20id", "a" * 81])
def test_get_unknown_or_invalid(client, rid):
    assert client.get(f"/v1/runs/{rid}").status_code in (404, 422)
    assert client.get(f"/v1/runs/{rid}").status_code == 404


@pytest.mark.parametrize(
    "path,ctype",
    [
        ("descriptions.vtt", "text/vtt"),
        ("described-audio.m4a", "audio/mp4"),
        ("described.mp4", "video/mp4"),
        ("keyframes/c001.jpg", "image/jpeg"),
    ],
)
def test_files_content_types(client, data, path, ctype, make_run):
    make_run(data / "runs", "r1")
    r = client.get(f"/v1/runs/r1/files/{path}")
    assert r.status_code == 200
    assert r.headers["content-type"].split(";")[0] == ctype


def test_report_html_headers(client, data, make_run):
    make_run(data / "runs", "r1")
    r = client.get("/v1/runs/r1/files/report.html")
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-security-policy"] == (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:"
    )
    assert r.headers["x-content-type-options"] == "nosniff"


def test_range_206(client, data, make_run):
    make_run(data / "runs", "r1")
    r = client.get("/v1/runs/r1/files/described.mp4", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206
    assert len(r.content) == 10
    assert r.headers["content-range"].startswith("bytes 0-9/")


@pytest.mark.parametrize(
    "path",
    [
        "secret.txt",
        "run.log",
        "keyframes/missing.jpg",
        "../r1/report.html",
        "%2e%2e/r1/report.html",
        "..%2fr1%2freport.html",
        "keyframes/..%2f..%2fsecret.txt",
        "%2Fetc%2Fpasswd",
        "keyframes%5C..%5Csecret.txt",
        "keyframes/../secret.txt",
        "keyframes/",
        "keyframes",
    ],
)
def test_files_rejected(client, data, path, make_run):
    make_run(data / "runs", "r1")
    assert client.get(f"/v1/runs/r1/files/{path}").status_code == 404


def test_files_unknown_run(client):
    assert client.get("/v1/runs/zzz/files/report.html").status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/v1/runs/nope",
        "/v1/runs/nope/files/report.html",
        "/v1/runs/r1/files/secret.txt",
        "/v1/runs/nope/cues/c001/audio",
        "/v1/runs/r1/cues/c999/audio",
        "/v1/runs/nope/original",
    ],
)
def test_not_found_has_the_error_shape(client, data, make_run, path):
    make_run(data / "runs", "r1")
    r = client.get(path)
    assert r.status_code == 404
    assert r.json()["error"] == "not_found" and set(r.json()) == {"error", "message"}


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_the_api_refuses_to_start_without_frame_verification(settings, monkeypatch, value, caplog):
    from fastapi.testclient import TestClient

    from lull.api.app import create_app

    monkeypatch.setenv("LULL_NO_VERIFY", value)
    with pytest.raises(RuntimeError, match="LULL_NO_VERIFY"):
        with TestClient(create_app(settings)):
            pass
    assert any("LULL_NO_VERIFY" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_a_false_no_verify_is_no_obstacle(settings, monkeypatch, value):
    from fastapi.testclient import TestClient

    from lull.api.app import create_app

    monkeypatch.setenv("LULL_NO_VERIFY", value)
    with TestClient(create_app(settings)) as c:
        assert c.get("/v1/samples").status_code == 200


def test_the_entrypoint_never_forwards_no_verify():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "docker" / "entrypoint.sh").read_text()
    assert "LULL_NO_VERIFY" in script and "unset LULL_NO_VERIFY" in script
