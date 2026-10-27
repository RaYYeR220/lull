"""Listen stage: transcript, speaker turns, speech spans, protected sound and usable gaps."""

from __future__ import annotations

from pathlib import Path
from statistics import median

from pydantic import BaseModel, Field

from lull.media import extract_wav, loudness_profile, probe
from lull.speech import SpeechClient, assign_speakers
from lull.timeline import find_gaps, speech_spans
from lull.types import Gap, Span, Turn, Word

FLOOR_DB = -120.0
EPS = 1e-6


class ListenResult(BaseModel):
    words: list[Word] = Field(default_factory=list)
    turns: list[Turn] = Field(default_factory=list)
    speech: list[Span] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    protected: list[Span] = Field(default_factory=list)
    duration: float
    has_audio: bool


def protected_spans(
    profile: list[tuple[float, float]],
    speech: list[Span],
    *,
    hop: float = 0.1,
    rise_db: float = 12.0,
    min_len: float = 0.3,
) -> list[Span]:
    """Loud non-speech runs (sound effects a describer must not talk over).

    Baseline is the median level of non-speech windows above the digital-silence floor;
    a window is loud at baseline + rise_db or more; runs shorter than min_len are dropped.
    """
    quiet = [
        (t, db) for t, db in profile if not any(t < s.end and t + hop > s.start for s in speech)
    ]
    audible = [db for _, db in quiet if db > FLOOR_DB]
    if not audible:
        return []
    threshold = median(audible) + rise_db
    out: list[Span] = []
    run: tuple[float, float] | None = None
    for t, db in quiet:
        if db >= threshold:
            if run is not None and abs(run[1] - t) < EPS:
                run = (run[0], t + hop)
            else:
                if run is not None:
                    out.append(Span(start=run[0], end=run[1]))
                run = (t, t + hop)
        elif run is not None:
            out.append(Span(start=run[0], end=run[1]))
            run = None
    if run is not None:
        out.append(Span(start=run[0], end=run[1]))
    return [s for s in out if s.dur >= min_len - EPS]


def listen(video: Path, work: Path, speech: SpeechClient, *, wps: float) -> ListenResult:
    info = probe(video)
    if not info.has_audio:
        return ListenResult(
            gaps=find_gaps([], info.duration, wps=wps),
            duration=info.duration,
            has_audio=False,
        )
    work.mkdir(parents=True, exist_ok=True)
    wav = extract_wav(video, work / "audio16k.wav")
    words = speech.transcribe(wav)
    turns = speech.diarize(wav)
    words = assign_speakers(words, turns)
    spans = speech_spans(words, turns)
    protected = protected_spans(loudness_profile(wav), spans)
    return ListenResult(
        words=words,
        turns=turns,
        speech=spans,
        gaps=find_gaps(spans, info.duration, protected=protected, wps=wps),
        protected=protected,
        duration=info.duration,
        has_audio=True,
    )
