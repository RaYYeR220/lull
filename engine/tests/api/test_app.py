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
    make_run(data / "runs", "old", created="2026-10-01T00:00:00+00:00")
    make_run(data / "runs", "new", created="2026-10-02T00:00:00+00:00")
    make_run(data / "runs", "bad", failed=True)
    make_run(data / "runs", "pending", manifest=False)
    items = client.get("/v1/runs").json()
    pub = [i["id"] for i in items if i["status"] == "done"]
    assert pub == ["show1", "new", "old"]
    assert items[0]["kind"] == "showcase" and items[1]["kind"] == "live"
    first = items[0]
    assert first["title"] == "Showcase" and first["duration"] == 90.0
    assert first["status"] == "done" and first["cost_usd"] == 0.03
    assert first["cues"]["verified"] == 5
    by_id = {i["id"]: i for i in items}
    assert by_id["bad"]["status"] == "failed"
    assert by_id["pending"]["status"] == "stopped"  # not being described here


def test_list_runs_caps_live_at_20(client, data, make_run):
    for i in range(25):
        make_run(data / "runs", f"r{i:02d}", created=f"2026-10-01T00:{i:02d}:00+00:00")
    assert len(client.get("/v1/runs").json()) == 20


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
