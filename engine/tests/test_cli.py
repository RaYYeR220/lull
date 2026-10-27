import json
import re
import time
import tomllib
import wave
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lull import cli
from lull.config import get_settings
from lull.ledger import CallRecord, Ledger
from lull.pipeline import run_id_for
from lull.store import file_sha256
from pipeline_fakes import CUE_TEXT, FakeSpeech, FakeTF

STAGES = ["listen", "watch", "research", "plan", "schedule", "finalize", "publish"]
runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """LULL_HOME in tmp_path: the real ledger and voices.json are never touched."""
    monkeypatch.setenv("LULL_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    yield tmp_path / "home"
    get_settings.cache_clear()


def _fake_clients(monkeypatch, ledger: Ledger) -> dict:
    made: dict = {}

    def clients(no_research: bool):
        made["no_research"] = no_research
        return ledger, FakeTF(ledger), FakeSpeech(), None

    monkeypatch.setattr(cli, "_clients", clients)
    return made


def _free_ledger(home: Path, **caps) -> Ledger:
    return Ledger(home / "ledger.db", **{"cap_run": 0, "cap_day": 0, "cap_total": 0, **caps})


def test_describe_prints_progress_and_a_summary(
    tone_slides, tmp_path, home, fast_media, monkeypatch
):
    made = _fake_clients(monkeypatch, _free_ledger(home))
    out = tmp_path / "out"
    args = [
        "describe",
        str(tone_slides),
        "--out",
        str(out),
        "--no-research",
        "--run-id",
        "demo",
        "--voice",
        "Aria",
        "--mode",
        "extended",
    ]
    r = runner.invoke(cli.app, args)
    assert r.exit_code == 0, r.output
    assert made == {"no_research": True}
    for stage in STAGES:  # each stage with its time
        assert re.search(rf"^{stage}\s+\d+\.\d s\b", r.output, re.M), stage
    assert re.search(r"^\s+call\s+plan\s+Ultra\b.*\$0\.\d{4}", r.output, re.M)
    assert re.search(
        rf"^\s+cue\s+c001\s+verified\s+0:01\.8\b.*{re.escape(CUE_TEXT['c001'])}", r.output, re.M
    )
    assert "! the recogniser stretched 'Bye.'" in r.output
    assert re.search(r"^\s*cues\s+2 verified, 0 held, 1 extended$", r.output, re.M)
    assert re.search(r"^\s*overlap\s+0\.000 s$", r.output, re.M)
    assert re.search(r"^\s*cost\s+\$0\.\d{4}\b", r.output, re.M)
    assert re.search(rf"^\s*output\s+{re.escape(str(out))}$", r.output, re.M)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "demo"
    assert (manifest["options"]["voice"], manifest["options"]["mode"]) == ("Aria", "extended")


def test_describe_defaults_run_id_and_output_folder(
    tone_slides, tmp_path, home, fast_media, monkeypatch
):
    made = _fake_clients(monkeypatch, _free_ledger(home))
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(cli.app, ["describe", str(tone_slides)])
    assert r.exit_code == 0, r.output
    assert made == {"no_research": False}
    run_id = run_id_for(tone_slides, file_sha256(tone_slides))
    assert (tmp_path / "runs" / run_id / "manifest.json").exists()
    assert "voice 'Sofia' is not calibrated" in r.output


def test_describe_stops_on_the_spend_cap(tone_slides, tmp_path, home, monkeypatch):
    _fake_clients(monkeypatch, _free_ledger(home, cap_run=0.01))  # 12 s needs $0.03
    r = runner.invoke(cli.app, ["describe", str(tone_slides), "--out", str(tmp_path / "o")])
    assert r.exit_code == 2
    assert "spend cap" in r.output and "run cap $0.01" in r.output
    assert not (tmp_path / "o" / "stages").exists()


def test_describe_rejects_an_unknown_mode_and_a_missing_video(tone_slides, tmp_path, home):
    r = runner.invoke(cli.app, ["describe", str(tone_slides), "--mode", "fast"])
    assert r.exit_code == 2
    r = runner.invoke(cli.app, ["describe", str(tmp_path / "nope.mp4")])
    assert r.exit_code == 2


def test_calibrate_voice_prints_and_stores_the_rate(tmp_path, home, monkeypatch):
    class Speech:
        def synthesize(self, text, voice, dst):
            with wave.open(str(dst), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(b"\0\0" * 22050 * 16)
            return Path(dst)

    monkeypatch.setattr(cli, "_speech", lambda: Speech())
    r = runner.invoke(cli.app, ["calibrate-voice", "--voice", "Aria"])
    assert r.exit_code == 0, r.output
    assert "Aria: 40 words in 16.00 s = 2.500 words/s" in r.output
    assert json.loads((home / "voices.json").read_text(encoding="utf-8")) == {"Aria": 2.5}


def _call(run_id, role, model, cost):
    return CallRecord(
        run_id=run_id,
        stage=role,
        role=role,
        provider="tokenfactory",
        model=model,
        tokens_in=1200,
        tokens_cached=200,
        tokens_out=300,
        latency_s=2.5,
        cost_usd=cost,
        ts=time.time(),
    )


def test_ledger_lists_runs_and_details_one(home):
    ledger = _free_ledger(home)
    ledger.record(_call("r1", "watch", "nvidia/Nemotron-3.5-Super-VL", 0.0062))
    ledger.record(_call("r1", "plan", "nvidia/Nemotron-3-Ultra-550b-a55b", 0.0158))
    ledger.record(_call("r2", "verify", "nvidia/Nemotron-3.5-Super-VL", 0.003))
    r = runner.invoke(cli.app, ["ledger"])
    assert r.exit_code == 0, r.output
    assert re.search(r"^\s*r1\s+2 calls\s+\$0\.0220", r.output, re.M)
    assert re.search(r"^\s*r2\s+1 calls?\s+\$0\.0030", r.output, re.M)
    assert re.search(r"today\s+\$0\.0250", r.output) and re.search(r"total\s+\$0\.0250", r.output)
    r = runner.invoke(cli.app, ["ledger", "--run", "r1"])
    assert r.exit_code == 0, r.output
    assert re.search(r"^\s*watch\s+Super VL\s+1\s+1,200\s+200\s+300\s+\$0\.0062", r.output, re.M)
    assert re.search(r"^\s*plan\s+Ultra\s+1\b.*\$0\.0158", r.output, re.M)
    assert re.search(r"^\s*total\s+2\b.*\$0\.0220", r.output, re.M)
    r = runner.invoke(cli.app, ["ledger", "--run", "nope"])
    assert r.exit_code == 1 and "no calls" in r.output


def test_lull_entry_point_is_declared():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    scripts = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["lull"] == "lull.cli:app"
