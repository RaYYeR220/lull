from pathlib import Path

import pytest

from lull.stages.listen import ListenResult, listen, protected_spans
from lull.timeline import find_gaps, speech_spans
from lull.types import Span, Turn, Word


class FakeSpeech:
    def __init__(self, words: list[Word] | None = None, turns: list[Turn] | None = None):
        self.words = words or []
        self.turns = turns or []
        self.calls: list[str] = []

    def transcribe(self, wav: Path) -> list[Word]:
        self.calls.append("transcribe")
        return list(self.words)

    def diarize(self, wav: Path) -> list[Turn]:
        self.calls.append("diarize")
        return list(self.turns)

    def synthesize(self, text: str, voice: str, dst: Path) -> Path:
        raise AssertionError("listen must not synthesize")


def _profile(levels: list[float], hop: float = 0.1) -> list[tuple[float, float]]:
    return [(i * hop, db) for i, db in enumerate(levels)]


def test_protected_loud_run_in_quiet():
    prof = _profile([-40.0] * 20 + [-20.0] * 5 + [-40.0] * 20)
    out = protected_spans(prof, [])
    assert len(out) == 1
    assert out[0].start == pytest.approx(2.0)
    assert out[0].end == pytest.approx(2.5)


def test_protected_short_run_dropped():
    prof = _profile([-40.0] * 20 + [-20.0] * 2 + [-40.0] * 20)
    assert protected_spans(prof, []) == []


def test_protected_ignores_speech_windows():
    prof = _profile([-40.0] * 20 + [-10.0] * 10 + [-40.0] * 20)
    assert protected_spans(prof, [Span(start=2.0, end=3.0)]) == []


def test_protected_all_floor_means_none():
    assert protected_spans(_profile([-120.0] * 30), []) == []


def test_protected_floor_windows_excluded_from_baseline():
    # mostly digital silence, one quiet hum run; floor must not drag the baseline down
    prof = _profile([-120.0] * 30 + [-40.0] * 10 + [-120.0] * 30)
    assert protected_spans(prof, []) == []


def test_protected_merges_consecutive_windows_only():
    prof = _profile([-40.0] * 10 + [-20.0] * 4 + [-40.0] * 3 + [-20.0] * 4 + [-40.0] * 10)
    out = protected_spans(prof, [])
    assert [(round(s.start, 1), round(s.end, 1)) for s in out] == [(1.0, 1.4), (1.7, 2.1)]


def test_listen_matches_find_gaps(tone_slides: Path, tmp_path: Path):
    words = [Word(text="hi", start=5.0, end=5.5), Word(text="there", start=5.6, end=6.0)]
    turns = [Turn(speaker=0, start=5.0, end=6.0)]
    fake = FakeSpeech(words, turns)
    res = listen(tone_slides, tmp_path, fake, wps=3.0)
    assert isinstance(res, ListenResult)
    assert (tmp_path / "audio16k.wav").exists()
    assert fake.calls == ["transcribe", "diarize"]
    assert res.has_audio is True
    assert res.duration == pytest.approx(12.0, abs=0.05)
    assert res.words[0].speaker == 0
    expected_speech = speech_spans(res.words, turns)
    assert res.speech == expected_speech
    assert res.gaps == find_gaps(res.speech, res.duration, protected=res.protected, wps=3.0)
    assert res.gaps  # silent tail after 6 s is usable
    assert ListenResult.model_validate_json(res.model_dump_json()) == res


def test_listen_loud_burst_one_protected_span(loud_burst: Path, tmp_path: Path):
    res = listen(loud_burst, tmp_path, FakeSpeech(), wps=3.0)
    assert len(res.protected) == 1
    p = res.protected[0]
    assert p.start == pytest.approx(2.0, abs=0.25)
    assert p.end == pytest.approx(3.0, abs=0.25)
    # burst splits the video into two usable gaps, neither overlapping it
    assert len(res.gaps) == 2
    for g in res.gaps:
        assert g.usable_end <= p.start + 1e-6 or g.usable_start >= p.end - 1e-6


def test_listen_no_audio_single_gap(noaudio: Path, tmp_path: Path):
    fake = FakeSpeech([Word(text="x", start=0, end=1)])
    res = listen(noaudio, tmp_path, fake, wps=3.0)
    assert fake.calls == []
    assert res.has_audio is False
    assert res.words == res.turns == res.speech == res.protected == []
    assert len(res.gaps) == 1
    assert res.gaps[0].usable_start == pytest.approx(0.2)
    assert res.gaps[0].usable_end == pytest.approx(res.duration - 0.2)
    assert not (tmp_path / "audio16k.wav").exists()


def test_listen_propagates_speech_errors(tone_slides: Path, tmp_path: Path):
    class Boom(FakeSpeech):
        def transcribe(self, wav: Path) -> list[Word]:
            raise RuntimeError("asr down")

    with pytest.raises(RuntimeError, match="asr down"):
        listen(tone_slides, tmp_path, Boom(), wps=3.0)
