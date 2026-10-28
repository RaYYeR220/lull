import json
import wave
from pathlib import Path

import pytest

from lull import voices
from lull.stages.plan import count_words
from lull.voices import REFERENCE, calibrate, voice_wps


def _wav(path: Path, seconds: float, rate: int = 22050) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * round(seconds * rate))
    return path


class FakeSpeech:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text, voice, dst):
        self.calls.append((text, voice))
        return _wav(Path(dst), self.seconds)


def test_reference_paragraph_is_forty_plain_words():
    assert count_words(REFERENCE) == 40
    assert not any(c.isdigit() for c in REFERENCE)  # numbers are spoken as several words


def test_defaults_without_a_calibration_file(tmp_path):
    path = tmp_path / "voices.json"
    assert voice_wps("Sofia", path) == (3.05, "default")  # measured on Magpie
    assert voice_wps("Aria", path) == (2.8, "fallback")
    assert voice_wps("Leo", path) == (2.8, "fallback")


def test_calibrate_measures_and_stores_the_rate(tmp_path):
    path = tmp_path / "home" / "voices.json"
    speech = FakeSpeech(16.0)
    cal = calibrate(speech, "Sofia", tmp_path / "work", path)
    assert speech.calls == [(REFERENCE, "Sofia")]
    assert (cal.voice, cal.words) == ("Sofia", 40)
    assert cal.seconds == pytest.approx(16.0, abs=1e-3)
    assert cal.wps == pytest.approx(2.5, abs=1e-3)
    assert json.loads(path.read_text(encoding="utf-8")) == {"Sofia": 2.5}
    assert voice_wps("Sofia", path) == (2.5, "calibrated")
    calibrate(FakeSpeech(20.0), "Aria", tmp_path / "work", path)  # merged, not replaced
    assert json.loads(path.read_text(encoding="utf-8")) == {"Aria": 2.0, "Sofia": 2.5}


def test_an_unreadable_calibration_file_is_an_error(tmp_path):
    path = tmp_path / "voices.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError, match="voices.json"):
        voice_wps("Sofia", path)
    path.write_text("[2.5]", encoding="utf-8")
    with pytest.raises(ValueError, match="voices.json"):
        voice_wps("Sofia", path)


def test_default_path_is_under_lull_home(tmp_path, monkeypatch):
    from lull.config import get_settings

    monkeypatch.setenv("LULL_HOME", str(tmp_path / "h"))
    get_settings.cache_clear()
    try:
        assert voices.voices_path() == tmp_path / "h" / "voices.json"
    finally:
        get_settings.cache_clear()


def test_calibrate_refuses_silent_audio(tmp_path):
    from lull.media import MediaError

    path = tmp_path / "voices.json"
    with pytest.raises(MediaError, match="produced no usable audio"):
        calibrate(FakeSpeech(0.0), "Sofia", tmp_path / "work", path)
    assert not path.exists()


@pytest.mark.parametrize("seconds", [5.0, 45.0])  # 8.0 and 0.89 words per second
def test_calibrate_refuses_an_implausible_rate_and_keeps_the_file(tmp_path, seconds):
    path = tmp_path / "voices.json"
    path.write_text('{"Aria": 2.4}\n', encoding="utf-8")
    with pytest.raises(voices.CalibrationError, match="words/s"):
        calibrate(FakeSpeech(seconds), "Sofia", tmp_path / "work", path)
    assert path.read_text(encoding="utf-8") == '{"Aria": 2.4}\n'


def test_a_corrupt_entry_is_skipped_with_a_warning(tmp_path, caplog):
    path = tmp_path / "voices.json"
    path.write_text('{"Sofia": -1, "Aria": 2.4, "Leo": "fast", "Max": 9.0}', encoding="utf-8")
    with caplog.at_level("WARNING", logger="lull.voices"):
        assert voice_wps("Aria", path) == (2.4, "calibrated")
        assert voice_wps("Sofia", path) == (3.05, "default")
        assert voice_wps("Max", path) == (2.8, "fallback")
    text = caplog.text
    assert "'Sofia'" in text and "'Leo'" in text and "'Max'" in text
    calibrate(FakeSpeech(16.0), "Leo", tmp_path / "work", path)  # rewritten without the junk
    assert json.loads(path.read_text(encoding="utf-8")) == {"Aria": 2.4, "Leo": 2.5}
