"""The read routes under abuse: caches, damaged manifests, odd paths, HEAD, CORS, and no local
paths in what a manifest returns."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

import lull.api
from lull.api import store as store_mod
from lull.api.health import ledger_budget, midnight_utc


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# -- /v1/runs: a short cache, only the newest folders parsed, damage tolerated ------------------


def test_run_list_is_cached_for_five_seconds(client, data, make_run):
    clock = Clock()
    client.app.state.store.clock = clock
    make_run(data / "runs", "first")
    assert [r["id"] for r in client.get("/v1/runs").json()] == ["first"]
    make_run(data / "runs", "second", created="2026-10-28T10:00:00+00:00")
    clock.t += 4.9
    assert [r["id"] for r in client.get("/v1/runs").json()] == ["first"]
    clock.t += 0.2
    assert [r["id"] for r in client.get("/v1/runs").json()] == ["second", "first"]


def test_run_list_parses_only_the_20_newest_folders(client, data, make_run, monkeypatch):
    now = time.time()
    for i in range(25):
        # r00..r04 claim the newest `created` but their folders are the oldest
        created = f"2026-10-{30 if i < 5 else 1:02d}T00:{i:02d}:00+00:00"
        d = make_run(data / "runs", f"r{i:02d}", created=created)
        mtime = now - 10_000 if i < 5 else now - 100 + i
        os.utime(d, (mtime, mtime))
    seen = []
    real = store_mod.RunStore.summary

    def counted(self, kind, d):
        seen.append(d.name)
        return real(self, kind, d)

    monkeypatch.setattr(store_mod.RunStore, "summary", counted)
    ids = [r["id"] for r in client.get("/v1/runs").json()]
    assert sorted(seen) == [f"r{i:02d}" for i in range(5, 25)]
    assert ids == [f"r{i:02d}" for i in range(24, 4, -1)]


def test_a_damaged_manifest_never_breaks_the_list(client, data, make_run):
    make_run(data / "runs", "good")
    junk = make_run(data / "runs", "junk", manifest=False)
    (junk / "manifest.json").write_text("{not json")
    listed = make_run(data / "runs", "listed", manifest=False)
    (listed / "manifest.json").write_text("[1, 2]")
    weird = make_run(data / "runs", "weird", manifest=False)
    (weird / "manifest.json").write_text(
        json.dumps({"video": "clip.mp4", "summary": [1], "created": 5, "outputs": "x"})
    )
    r = client.get("/v1/runs")
    assert r.status_code == 200
    by_id = {i["id"]: i for i in r.json()}
    assert by_id["good"]["status"] == "published"
    assert by_id["junk"]["status"] == "failed"
    assert by_id["listed"]["status"] == "failed"
    w = by_id["weird"]
    assert w["status"] == "published" and w["title"] == "weird"
    assert w["duration"] is None and w["cost_usd"] is None and isinstance(w["created"], str)
    assert client.get("/v1/runs/junk").status_code == 404
    assert client.get("/v1/runs/listed").status_code == 404
    assert client.get("/v1/runs/junk/files/report.html").status_code == 404
    detail = client.get("/v1/runs/weird")
    assert detail.status_code == 200
    assert detail.json()["manifest"]["outputs"] == {}


def test_a_summary_that_raises_skips_only_that_run(client, data, make_run, monkeypatch):
    make_run(data / "runs", "good")
    make_run(data / "runs", "boom")
    real = store_mod.RunStore.summary

    def flaky(self, kind, d):
        if d.name == "boom":
            raise RuntimeError("disk said no")
        return real(self, kind, d)

    monkeypatch.setattr(store_mod.RunStore, "summary", flaky)
    r = client.get("/v1/runs")
    assert r.status_code == 200
    assert [i["id"] for i in r.json()] == ["good"]


# -- the manifest: read once per change, returned without local paths -----------------------


def test_manifest_is_parsed_once_until_it_changes(client, data, make_run, monkeypatch):
    d = make_run(data / "runs", "r1")
    reads = []
    real = store_mod._read_json

    def counted(path):
        reads.append(Path(path).name)
        return real(path)

    monkeypatch.setattr(store_mod, "_read_json", counted)
    for _ in range(3):
        assert client.get("/v1/runs/r1/files/described.mp4").status_code == 200
    assert reads.count("manifest.json") == 1
    m = json.loads((d / "manifest.json").read_text())
    m["outputs"].pop("described.mp4")
    (d / "manifest.json").write_text(json.dumps(m, indent=1))
    assert client.get("/v1/runs/r1/files/described.mp4").status_code == 404
    assert reads.count("manifest.json") == 2


def test_manifest_fields_are_whitelisted_and_paths_stripped(client, data, make_run, tmp_path):
    d = make_run(data / "runs", "r1")
    m = json.loads((d / "manifest.json").read_text())
    local = str(tmp_path / "work" / "clip.mp4")
    m["local_dir"] = str(d)
    m["video"]["path"] = local
    m["options"] = {"mode": "standard", "voice": "Sofia", "secret": "x"}
    m["summary"]["extended_error"] = f"ffmpeg failed on {local}: Invalid data"
    m["summary"]["debug"] = {"dir": str(d)}
    m["outputs"]["../outside.txt"] = {"sha256": "x", "bytes": 1}
    m["outputs"]["C:/Windows/win.ini"] = {"sha256": "x", "bytes": 1}
    m["versions"] = {"engine": "0.1.0", "models": ["m"], "tts": "t", "host": str(d)}
    (d / "manifest.json").write_text(json.dumps(m))
    j = client.get("/v1/runs/r1").json()
    man = j["manifest"]
    assert set(man) <= {"run_id", "created", "video", "options", "outputs", "summary", "versions"}
    assert set(man["video"]) == {"name", "duration", "sha256"}
    assert man["options"] == {"mode": "standard", "voice": "Sofia"}
    assert "debug" not in man["summary"]
    assert man["summary"]["extended_error"] == "ffmpeg failed on clip.mp4: Invalid data"
    assert set(man["outputs"]) == set(m["outputs"]) - {"../outside.txt", "C:/Windows/win.ini"}
    assert man["versions"] == {"engine": "0.1.0", "models": ["m"], "tts": "t"}
    assert j["dirty"] is False
    text = json.dumps(j)
    for root in (str(tmp_path), tmp_path.as_posix(), str(tmp_path).replace("\\", "\\\\")):
        assert root not in text


# -- files: odd paths ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "described.mp4::$DATA",
        "keyframes/c001.jpg::$DATA",
        "keyframes/c001.jpg:alt",
        "C:/Windows/win.ini",
        "keyframes/notes.txt",
        "keyframes/c001.JPG.exe",
    ],
)
def test_files_colons_and_non_keyframes_rejected(client, data, path, make_run):
    d = make_run(data / "runs", "r1")
    (d / "keyframes" / "notes.txt").write_text("not a keyframe")
    assert client.get(f"/v1/runs/r1/files/{path}").status_code == 404


def test_files_manifest_entries_that_leave_the_run_are_refused(client, data, make_run):
    d = make_run(data / "runs", "r1")
    (data / "runs" / "outside.txt").write_text("outside")
    m = json.loads((d / "manifest.json").read_text())
    for name in ("../outside.txt", "/etc/passwd", "C:/Windows/win.ini", "sub/../secret.txt"):
        m["outputs"][name] = {"sha256": "x", "bytes": 1}
    (d / "manifest.json").write_text(json.dumps(m))
    for name in ("../outside.txt", "%2e%2e/outside.txt", "%2Fetc%2Fpasswd", "sub/../secret.txt"):
        assert client.get(f"/v1/runs/r1/files/{name}").status_code == 404, name


def test_files_symlink_out_of_the_run_is_refused(client, data, make_run):
    d = make_run(data / "runs", "r1")
    outside = data / "outside.jpg"
    outside.write_bytes(b"\xff\xd8 outside")
    link = d / "keyframes" / "evil.jpg"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges here")
    assert client.get("/v1/runs/r1/files/keyframes/evil.jpg").status_code == 404


@pytest.mark.parametrize("rid", [".", ".."])
def test_dot_ids_are_not_runs(client, data, make_run, rid, settings):
    make_run(data / "runs", "r1")
    assert store_mod.RunStore(settings).find(rid) is None
    enc = rid.replace(".", "%2e")
    assert client.get(f"/v1/runs/{enc}").status_code == 404
    assert client.get(f"/v1/runs/{enc}/files/report.html").status_code == 404
    assert client.get(f"/v1/runs/{enc}/files/r1/report.html").status_code == 404


def test_head_on_files(client, data, make_run):
    make_run(data / "runs", "r1")
    r = client.head("/v1/runs/r1/files/described.mp4")
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert int(r.headers["content-length"]) == 256 * 16
    assert r.content == b""
    assert client.head("/v1/runs/r1/files/secret.txt").status_code == 404


# -- CORS -------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PATCH"])
def test_cors_preflight_allows_the_methods_in_use(client, method):
    r = client.options(
        "/v1/runs/r1/cues/c001",
        headers={"Origin": "http://web.test", "Access-Control-Request-Method": method},
    )
    assert r.status_code == 200
    assert method in r.headers["access-control-allow-methods"]


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_cors_preflight_refuses_other_methods(client, method):
    r = client.options(
        "/v1/runs/r1",
        headers={"Origin": "http://web.test", "Access-Control-Request-Method": method},
    )
    assert r.status_code == 400


# -- /v1/health: cached, read-only ledger -------------------------------------------------------


def test_health_is_cached_for_ten_seconds(client, monkeypatch):
    import lull.speech

    calls = []
    monkeypatch.setattr(lull.speech.NemoSpeechHTTP, "health", lambda self: calls.append(1) or True)
    clock = Clock()
    client.app.state.health.clock = clock
    for _ in range(3):
        assert client.get("/v1/health").json()["speech"] is True
    assert len(calls) == 1
    clock.t += 10.1
    client.get("/v1/health")
    assert len(calls) == 2


def test_health_never_creates_the_ledger(client, tmp_path, monkeypatch):
    import lull.speech

    monkeypatch.setattr(lull.speech.NemoSpeechHTTP, "health", lambda self: False)
    j = client.get("/v1/health").json()
    assert j["budget"]["spent_today"] == 0.0 and j["budget"]["spent_total"] == 0.0
    assert not (tmp_path / "home" / "ledger.db").exists()
    assert not (tmp_path / "home").exists()


def test_ledger_budget_reads_spend(tmp_path):
    from lull.ledger import CallRecord, Ledger

    path = tmp_path / "ledger.db"
    ledger = Ledger(path, cap_run=0, cap_day=0, cap_total=0)
    rec = dict(stage="watch", role="watch", provider="tokenfactory", model="m", tokens_in=1,
               tokens_cached=0, tokens_out=1, latency_s=0.1)  # fmt: skip
    ledger.record(CallRecord(run_id="a", cost_usd=0.25, ts=time.time(), **rec))
    ledger.record(CallRecord(run_id="b", cost_usd=0.5, ts=midnight_utc() - 60, **rec))
    b = ledger_budget(path, cap_day=3.0, cap_total=35.0)
    assert b == {"spent_today": 0.25, "cap_day": 3.0, "spent_total": 0.75, "cap_total": 35.0}


def test_ledger_budget_survives_a_damaged_ledger(tmp_path):
    path = tmp_path / "ledger.db"
    path.write_bytes(b"this is not sqlite" * 100)
    b = ledger_budget(path, cap_day=3.0, cap_total=35.0)
    assert b == {"spent_today": None, "cap_day": 3.0, "spent_total": None, "cap_total": 35.0}
    con = sqlite3.connect(tmp_path / "other.db")
    con.execute("CREATE TABLE unrelated (x)")
    con.commit()
    con.close()
    assert ledger_budget(tmp_path / "other.db", cap_day=1.0, cap_total=2.0)["spent_total"] is None


def test_health_with_a_damaged_ledger_still_answers(client, tmp_path, monkeypatch):
    import lull.speech

    monkeypatch.setattr(lull.speech.NemoSpeechHTTP, "health", lambda self: True)
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "ledger.db").write_bytes(b"garbage" * 50)
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["budget"]["spent_total"] is None


def test_midnight_utc():
    at = datetime(2026, 10, 27, 15, 30, 12, tzinfo=UTC)
    assert midnight_utc(at) == datetime(2026, 10, 27, tzinfo=UTC).timestamp()


def test_api_uses_no_private_ledger_helpers():
    root = Path(lull.api.__file__).parent
    for src in root.glob("*.py"):
        assert "_midnight_utc" not in src.read_text(encoding="utf-8"), src.name


# -- the scrubber: URLs stay readable, a root is cut only as whole path parts ---------------------

LEGIT_URLS = [
    "https://huggingface.co/datasets/nvidia/Nemotron-Personas-USA",
    "https://example.com/tmpl/page.html",
]


def _scrubber(**kw):
    from lull.api.clean import Scrubber

    return Scrubber(Path("/data/runs/r1"), [Path("/data"), Path("/tmp"), Path("/root")], **kw)


@pytest.mark.parametrize("url", LEGIT_URLS)
@pytest.mark.parametrize("names_only", [False, True])
def test_scrubber_keeps_legitimate_urls_readable(url, names_only):
    s = _scrubber(names_only=names_only)
    assert s(url) == url
    assert s(f"fetched {url} (404)") == f"fetched {url} (404)"


def test_scrubber_cuts_roots_only_at_path_boundaries():
    s = _scrubber()
    assert s("failed: /data/runs/r1/input/clip.mp4: bad") == "failed: input/clip.mp4: bad"
    assert s("see '/tmp/lull-x.log'") == "see 'lull-x.log'"
    assert s("open /database/table.db") == "open table.db"  # not "base/table.db"
    assert s("in /rooted/thing.txt now") == "in thing.txt now"  # not "ed/thing.txt"
    assert s("(/data)") == "()"
    assert _scrubber(names_only=True)("/data/runs/r1/input/clip.mp4") == "clip.mp4"


# -- /v1/health does not make callers wait on a slow speech probe ---------------------------------


def test_health_does_not_hold_its_lock_across_the_speech_probe(monkeypatch):
    import threading

    from lull.api import health as health_mod

    entered, gate = threading.Event(), threading.Event()
    calls = []

    def probe():
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert gate.wait(10)
        return True

    monkeypatch.setattr(health_mod, "speech_ok", probe)
    h = health_mod.Health(ttl=0.0)
    t = threading.Thread(target=h.get)
    t.start()
    try:
        assert entered.wait(10)
        done = threading.Event()
        threading.Thread(target=lambda: (h.get(), done.set())).start()
        assert done.wait(2), "a second caller waited for the first one's speech probe"
    finally:
        gate.set()
        t.join(10)


# -- the scrubber: every form of a root, whatever comes before it ---------------------------------


def _win_scrubber():
    from lull.api.clean import Scrubber

    return Scrubber(
        Path(r"C:\Users\egori\data\runs\r1"), [Path(r"C:\Users\egori"), Path("/home/app")]
    )


@pytest.mark.parametrize(
    "text",
    [
        r"error:C:\Users\egori\secret\x.log",
        r"\\?\C:\Users\egori\secret\x.log",
        "file:///C:/Users/egori/secret/x.log",
        "x-/home/app/secret/x.log",
        "see c:/users/EGORI/secret/x.log",
        "msys /c/Users/egori/secret/x.log",
        r"a.C:\Users\egori\secret\x.log",
    ],
)
def test_scrubber_cuts_every_form_of_a_root(text):
    out = _win_scrubber()(text)
    assert "x.log" in out
    assert "egori" not in out.lower() and "/home/app" not in out


@pytest.mark.parametrize(
    "text",
    [
        r"GET http://x.test/?p=C:\Users\egori\secret\x.log failed",
        "see file://host/C:/Users/egori/secret/x.log",
        "see file://fileserver/home/app/secret/x.log",
        "open http://C:/Users/egori/secret/x.log",
        "https://example.org/home/app/x.log",
    ],
)
def test_no_known_root_survives_inside_a_url(text):
    out = _win_scrubber()(text)
    assert "egori" not in out.lower() and "/home/app" not in out


def test_a_root_right_after_a_url_host_leaves_a_readable_url():
    assert _scrubber()("https://example.org/data/report.html") == "https://example.org/report.html"
