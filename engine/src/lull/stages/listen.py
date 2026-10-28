"""Listen stage: transcript, speaker turns, speech spans, protected sound and usable gaps."""

from __future__ import annotations

import logging
from pathlib import Path
from statistics import median

from pydantic import BaseModel, Field

from lull.media import extract_wav, loudness_profile, probe
from lull.speech import SpeechClient, assign_speakers
from lull.timeline import find_gaps, speech_spans
from lull.types import Gap, Span, Turn, Word

log = logging.getLogger(__name__)

FLOOR_DB = -120.0
EPS = 1e-6
# A word is implausible beyond max(2.0 s, 0.25 s per character); it is then cut to 0.09 s per
# character + 0.25 s, kept within 0.3..1.5 s.
IMPLAUSIBLE_S, IMPLAUSIBLE_S_PER_CHAR = 2.0, 0.25
WORD_S_PER_CHAR, WORD_BASE_S, WORD_MIN_S, WORD_MAX_S = 0.09, 0.25, 0.3, 1.5


class ClampedWord(BaseModel):
    """A word the recogniser stretched over silence, as it was given and where it now ends."""

    text: str
    start: float
    end: float
    clamped_end: float


class ListenResult(BaseModel):
    words: list[Word] = Field(default_factory=list)
    turns: list[Turn] = Field(default_factory=list)
    speech: list[Span] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    protected: list[Span] = Field(default_factory=list)
    duration: float
    has_audio: bool
    clamped: list[ClampedWord] = Field(default_factory=list)


def clamp_words(words: list[Word]) -> tuple[list[Word], list[ClampedWord]]:
    """Words with implausible durations cut back, and the ones that were cut.

    The recogniser sometimes stretches a word over a long silence (Parakeet gave "way." 17.2 to
    35.76 s on Elephants Dream), which would count that silence as speech. A word longer than
    max(2.0, 0.25 x characters) seconds is implausible and ends clamp(0.09 x characters + 0.25,
    0.3, 1.5) seconds after it starts; every other word, a drawn-out last word of a sentence
    included, keeps its end (the guard around speech stays whole).
    """
    out: list[Word] = []
    clamped: list[ClampedWord] = []
    for w in words:
        chars = len(w.text)
        if w.end - w.start > max(IMPLAUSIBLE_S, IMPLAUSIBLE_S_PER_CHAR * chars) + EPS:
            longest = min(WORD_MAX_S, max(WORD_MIN_S, WORD_S_PER_CHAR * chars + WORD_BASE_S))
            end = round(w.start + longest, 3)
            log.warning(
                "listen: clamped %r %.3f-%.3f s to end at %.3f s", w.text, w.start, w.end, end
            )
            clamped.append(ClampedWord(text=w.text, start=w.start, end=w.end, clamped_end=end))
            w = w.model_copy(update={"end": end})
        out.append(w)
    return out, clamped


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
    wav = extract_wav(video, work / "audio16k.wav", seconds=info.duration)
    words, clamped = clamp_words(speech.transcribe(wav))
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
        clamped=clamped,
    )
