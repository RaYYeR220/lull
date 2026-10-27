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


class Shot(BaseModel):
    index: int
    start: float
    end: float


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


class Cue(BaseModel):
    id: str
    gap_id: str | None
    start: float
    end: float | None
    text: str
    event_ids: list[str]
    status: Literal["verified", "held", "edited", "extended"]
    ahead: bool
    claims: list[Claim]
    audio_path: str | None
    audio_dur: float | None
    tempo: float = 1.0


class RunOptions(BaseModel):
    mode: Literal["standard", "extended"] = "standard"
    profile: Literal["education", "entertainment"] = "education"
    voice: str = "Sofia"  # Magpie TTS voice name
    source_url: str | None = None
    language: str = "en"
