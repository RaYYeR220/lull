"""Core data types. All times are seconds as float."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Span(BaseModel):
    start: float
    end: float

    @model_validator(mode="after")
    def _ordered(self) -> Span:
        if self.end < self.start:
            raise ValueError("end must be >= start")
        return self

    @property
    def dur(self) -> float:
        return self.end - self.start


class Word(BaseModel):
    text: str
    start: float
    end: float
    speaker: int | None = None


class Turn(BaseModel):
    speaker: int
    start: float
    end: float


class Gap(BaseModel):
    id: str
    start: float
    end: float
    usable_start: float  # after guards
    usable_end: float
    capacity_words: int
    protected: list[Span] = Field(default_factory=list)


EventKind = Literal[
    "setting",
    "person",
    "action",
    "on_screen_text",
    "slide",
    "chart",
    "object",
    "scene_change",
    "gesture",
]


class VisualEvent(BaseModel):
    id: str
    t_start: float
    t_end: float
    kind: EventKind
    text: str
    verbatim_text: str | None
    salience: int = Field(ge=1, le=5)
    said_in_dialogue: bool


class Variant(BaseModel):
    event_id: str
    length: Literal["short", "medium", "long"]
    text: str
    words: int


class Claim(BaseModel):
    text: str
    verdict: Literal["supported", "not_visible", "contradicted"]
    at: float | None
    span: str | None = None  # the exact words of the cue text the claim comes from
    evidence: str | None = None  # what the frames showed, in the vision model's words


class Check(BaseModel):
    """One verification round of a cue: the text the frames were checked against and the
    verdict on each of its claims."""

    text: str
    claims: list[Claim]


class Cue(BaseModel):
    id: str
    gap_id: str | None
    start: float
    end: float | None
    text: str
    event_ids: list[str]
    # "unverified": voiced with claim verification turned off (LULL_NO_VERIFY, evaluation)
    status: Literal["verified", "held", "edited", "extended", "approved", "unverified"]
    ahead: bool
    claims: list[Claim]
    audio_path: str | None
    audio_dur: float | None
    tempo: float = 1.0
    findings: list[dict] = Field(default_factory=list)  # lint findings, serialised
    note: str | None = None  # why the cue is held, or what finalisation changed
    checks: list[Check] = Field(default_factory=list)  # every verification round, in order
    parts: dict[str, str] = Field(default_factory=dict)  # event id -> the words composed for it
    described: list[str] | None = None  # events the final text still describes (None: all)


class RunOptions(BaseModel):
    mode: Literal["standard", "extended"] = "standard"
    profile: Literal["education", "entertainment"] = "education"
    voice: str = "Sofia"  # Magpie TTS voice name
    source_url: str | None = None
    language: str = "en"
