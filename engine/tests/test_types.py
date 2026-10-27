import pytest
from pydantic import ValidationError

from lull.config import Settings, get_settings
from lull.types import Claim, Cue, RunOptions, Span, VisualEvent


def test_span_dur():
    assert Span(start=1.0, end=3.5).dur == 2.5


def test_span_end_before_start_rejected():
    with pytest.raises(ValidationError):
        Span(start=3, end=1)


def test_visual_event_salience_bounds():
    base = dict(
        id="e1",
        t_start=0.0,
        t_end=1.0,
        kind="action",
        text="x",
        verbatim_text=None,
        said_in_dialogue=False,
    )
    assert VisualEvent(salience=5, **base).salience == 5
    with pytest.raises(ValidationError):
        VisualEvent(salience=6, **base)
    with pytest.raises(ValidationError):
        VisualEvent(salience=0, **base)


def test_cue_json_round_trip():
    cue = Cue(
        id="c1",
        gap_id="g1",
        start=2.0,
        end=4.5,
        text="A woman opens a door.",
        event_ids=["e1"],
        status="verified",
        ahead=False,
        claims=[Claim(text="a woman", verdict="supported", at=2.1)],
        audio_path=None,
        audio_dur=None,
    )
    assert cue.tempo == 1.0
    assert Cue.model_validate_json(cue.model_dump_json()) == cue


def test_run_options_defaults():
    o = RunOptions()
    assert (o.mode, o.profile, o.voice, o.source_url, o.language) == (
        "standard",
        "education",
        "Sofia",
        None,
        "en",
    )


def test_settings_load_without_env(monkeypatch, tmp_path):
    for k in (
        "NEBIUS_API_KEY",
        "TAVILY_API_KEY",
        "LULL_SPEECH_URL",
        "LULL_SPEECH_KEY",
        "LULL_CAP_RUN",
        "LULL_CAP_DAY",
        "LULL_CAP_TOTAL",
        "LULL_HOME",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=None)
    assert s.nebius_api_key is None and s.tavily_api_key is None
    assert s.lull_speech_url is None and s.lull_speech_key is None
    assert (s.lull_cap_run, s.lull_cap_day, s.lull_cap_total) == (0.50, 3.00, 35.00)
    assert s.lull_home.name == ".lull"


def test_settings_env_override_and_cache(monkeypatch):
    monkeypatch.setenv("LULL_CAP_RUN", "0.25")
    get_settings.cache_clear()
    assert get_settings().lull_cap_run == 0.25
    assert get_settings() is get_settings()
    get_settings.cache_clear()


def test_settings_accept_field_names(monkeypatch):
    monkeypatch.delenv("LULL_CAP_RUN", raising=False)
    s = Settings(_env_file=None, lull_cap_run=1.25)
    assert s.lull_cap_run == 1.25
