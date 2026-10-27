"""The pipeline: every stage in order, each cached, spend checked first, progress as events.

    probe -> spend check -> listen -> split_gaps -> watch -> research -> plan -> schedule
    -> compose_cues -> finalize (standard cues in time order, then extended cues)
    -> assert_no_overlap -> publish (mix included)

One voice rate (words per second, calibrated per voice; see lull.voices) times everything: the
gap capacities listen computes, the windows split_gaps cuts and the scheduler's sentence
deadlines. Each stage's result is cached in the run directory under a key over its inputs
(lull.store), finalize cue by cue, so a run stopped by a spend cap or a crash resumes where it
stopped and a finished run repeats without a model call. Before anything runs the ledger must
allow an estimate of $0.05 a minute + $0.02; a cap or a provider 402 mid-run raises
BudgetExceeded with every finished stage kept.

Progress goes to `on_event` as Event(type, stage, data, ts): stage_start / stage_end (seconds,
cached, counts), a `call` per ledger record, a `cue` per finalised cue, `warning`s (held cues,
clamped words, research problems, an uncalibrated voice), `error` (then the exception
propagates) and `done` with the published summary. Events are delivered one at a time even when
calls are recorded from other threads; a callback that raises is logged and never stops a run.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError

import lull
from lull import media
from lull.ledger import CallRecord, Ledger
from lull.speech import SpeechClient
from lull.stages import mix as mixer
from lull.stages.finalize import CueContext, assert_no_overlap, finalize_cue, names_in
from lull.stages.listen import ListenResult, listen
from lull.stages.plan import PlanResult, plan
from lull.stages.publish import PublishResult, publish
from lull.stages.research import ResearchResult, research
from lull.stages.schedule import Placement, compose_cues, placement_report, schedule
from lull.stages.watch import watch
from lull.store import Store, file_sha256, stage_key
from lull.tavily import Tavily
from lull.tf import TokenFactory
from lull.timeline import split_gaps
from lull.types import Cue, Gap, RunOptions, VisualEvent, Word
from lull.voices import voice_wps

log = logging.getLogger(__name__)

EventType = Literal["stage_start", "stage_end", "call", "cue", "warning", "error", "done"]
ESTIMATE_PER_MIN, ESTIMATE_BASE = 0.05, 0.02  # USD the spend check reserves for a run
WORK_DIR = "work"
M = TypeVar("M", bound=BaseModel)


@dataclass(frozen=True)
class Event:
    type: EventType
    stage: str | None
    data: dict = field(default_factory=dict)
    ts: float = 0.0


class _Events(BaseModel):
    events: list[VisualEvent]


class _Schedule(BaseModel):
    placements: list[Placement]
    extended: list[str]  # salient events no gap could hold, by start
    report: dict


def budget_estimate(duration_s: float) -> float:
    """USD the spend check reserves before a run: $0.05 a minute of video + $0.02."""
    return ESTIMATE_PER_MIN * max(0.0, duration_s) / 60 + ESTIMATE_BASE


def run_id_for(video: Path, sha256: str) -> str:
    """The default run id: the video's file stem and the first 8 hex digits of its sha256."""
    return f"{Path(video).stem}-{sha256[:8]}"


# -- who has been introduced -------------------------------------------------------------------


def introduced_before(
    start: float,
    names: Iterable[str],
    words: list[Word],
    events: list[VisualEvent],
    heard: Iterable[Cue],
) -> set[str]:
    """The names (whole words, case-sensitive) a listener already met at `start`: said in a
    transcript word that ends before it, in the on-screen text of an event that starts before
    it, or in a voiced description that starts before it (R15: introduced in dialogue, on
    screen or by an earlier description)."""
    names = {n for n in names if n}
    said = " ".join(w.text for w in words if w.end < start)
    texts = [said]
    texts += [e.verbatim_text for e in events if e.verbatim_text and e.t_start < start]
    texts += [c.text for c in heard if c.start < start]
    return set().union(*(names_in(t, names) for t in texts))


def heard_before(cue: Cue, finished: Iterable[Cue]) -> list[Cue]:
    """The finished cues a listener has heard when `cue` starts: voiced standard cues, and for
    an extended cue the voiced extended cues as well (the extended version has both)."""
    finished = list(finished)
    heard = mixer.voiced(finished)
    if cue.gap_id is None:
        heard += mixer.voiced_extended(finished)
    return [c for c in heard if c.start < cue.start]


def cue_context(
    cue: Cue,
    *,
    wps: float,
    duration: float,
    cast: dict[str, str],
    names: set[str],
    words: list[Word],
    events: list[VisualEvent],
    finished: list[Cue],
) -> CueContext:
    """What finalising `cue` needs to know about the rest of the programme. `names` (cast
    names and glossary spellings) are both the names lint checks and the proper nouns."""
    by_id = {e.id: e for e in events}
    own = sorted((by_id[i] for i in cue.event_ids if i in by_id), key=lambda e: (e.t_start, e.id))
    heard = heard_before(cue, finished)
    return CueContext(
        wps=wps,
        cast=dict(cast),
        named=set(names),
        introduced=introduced_before(cue.start, names, words, events, heard),
        verbatim=[e.verbatim_text for e in own if e.verbatim_text],
        proper_nouns=set(names),
        video_duration=duration,
    )


# -- the run -----------------------------------------------------------------------------------


class _Emitter:
    """Builds events and hands them to the callback one at a time."""

    def __init__(self, on_event: Callable[[Event], None]) -> None:
        self._on_event = on_event
        self._lock = threading.Lock()

    def __call__(self, type_: EventType, stage: str | None, **data) -> None:
        event = Event(type=type_, stage=stage, data=data, ts=time.time())
        with self._lock:
            try:
                self._on_event(event)
            except Exception:
                log.exception("pipeline: the event callback failed on a %s event", type_)


def _ignore(event: Event) -> None:
    pass


@dataclass
class _Run:
    video: Path
    run_dir: Path
    opts: RunOptions
    run_id: str
    sha256: str
    wps: float
    duration: float
    tf: TokenFactory
    speech: SpeechClient
    tavily: Tavily | None
    ledger: Ledger
    emit: _Emitter
    stage: str | None = None

    @property
    def store(self) -> Store:
        return Store(self.run_dir)

    @property
    def work(self) -> Path:
        return self.run_dir / WORK_DIR

    def key(self, stage: str, inputs: dict) -> str:
        return stage_key(
            {"stage": stage, "engine": lull.__version__, "video": self.sha256, "inputs": inputs}
        )

    @contextmanager
    def step(self, name: str) -> Iterator[dict]:
        """stage_start, then stage_end with its seconds and whatever the body put in the dict
        (no stage_end when the body raises: the run reports an error instead)."""
        self.stage = name
        self.emit("stage_start", name)
        info: dict = {"cached": False}
        t0 = time.monotonic()
        yield info
        self.emit("stage_end", name, seconds=round(time.monotonic() - t0, 3), **info)

    def cached(
        self, info: dict, name: str, inputs: dict, model: type[M], compute: Callable[[], M]
    ) -> M:
        """The stage result from the store when its inputs are unchanged, else computed and
        stored."""
        key = self.key(name, inputs)
        data = self.store.cached(name, key)
        if data is not None:
            try:
                result = model.model_validate(data)
            except ValidationError as e:
                log.warning("pipeline: cached %s does not load, recomputing: %s", name, e)
            else:
                info["cached"] = True
                return result
        result = compute()
        self.store.save(name, key, result.model_dump(mode="json"))
        return result

    def warn(self, kind: str, message: str, stage: str | None = None, **data) -> None:
        self.emit("warning", stage or self.stage, kind=kind, message=message, **data)

    # -- stages ------------------------------------------------------------------------------

    def listen(self) -> tuple[ListenResult, list[Gap]]:
        with self.step("listen") as info:
            heard = self.cached(
                info,
                "listen",
                {"wps": self.wps},
                ListenResult,
                lambda: listen(self.video, self.work, self.speech, wps=self.wps),
            )
            gaps = split_gaps(heard.gaps, wps=self.wps)
            info.update(
                words=len(heard.words),
                speakers=len({t.speaker for t in heard.turns}),
                speech_s=round(sum(s.dur for s in heard.speech), 3),
                gaps=len(heard.gaps),
                windows=len(gaps),
                capacity_words=sum(g.capacity_words for g in gaps),
                clamped=len(heard.clamped),
                has_audio=heard.has_audio,
            )
        for c in heard.clamped:
            self.warn(
                "clamped_word",
                f"the recogniser stretched {c.text!r} over {c.end - c.start:.2f} s from "
                f"{c.start:.2f} s; it now ends at {c.clamped_end:.2f} s",
                "listen",
                text=c.text,
                start=c.start,
                end=c.end,
                clamped_end=c.clamped_end,
            )
        return heard, gaps

    def watch(self, words: list[Word]) -> list[VisualEvent]:
        with self.step("watch") as info:
            inputs = {"words": words, "profile": self.opts.profile}
            out = self.cached(
                info,
                "watch",
                inputs,
                _Events,
                lambda: _Events(
                    events=watch(
                        self.video,
                        self.work,
                        words,
                        self.tf,
                        run_id=self.run_id,
                        profile=self.opts.profile,
                    )
                ),
            )
            info["events"] = len(out.events)
        return out.events

    def research(self, events: list[VisualEvent], words: list[Word]) -> ResearchResult:
        with self.step("research") as info:
            inputs = {
                "events": events,
                "words": words,
                "source_url": self.opts.source_url,
                "tavily": self.tavily is not None,
            }
            found = self.cached(
                info,
                "research",
                inputs,
                ResearchResult,
                lambda: research(
                    events,
                    words,
                    self.opts,
                    self.tf,
                    self.tavily,
                    run_id=self.run_id,
                ),
            )
            info.update(terms=len(found.glossary), skipped=found.skipped, errors=len(found.errors))
        if found.skipped:
            self.warn(
                "research_skipped",
                "research skipped: no Tavily lookup, names are spelled as heard and read",
                "research",
            )
        for err in found.errors:
            self.warn("research_error", f"research: {err}", "research")
        return found

    def plan(self, events, words, gaps, found: ResearchResult) -> PlanResult:
        with self.step("plan") as info:
            inputs = {
                "events": events,
                "words": words,
                "gaps": gaps,
                "research": found,
                "profile": self.opts.profile,
                "mode": self.opts.mode,
                "language": self.opts.language,
            }
            planned = self.cached(
                info,
                "plan",
                inputs,
                PlanResult,
                lambda: plan(
                    events,
                    words,
                    gaps,
                    found,
                    self.opts,
                    self.tf,
                    run_id=self.run_id,
                ),
            )
            info.update(
                variants=len(planned.variants),
                events=len(planned.priority),
                cast=sorted(planned.cast),
            )
        return planned

    def schedule(self, events, planned: PlanResult, gaps: list[Gap]) -> _Schedule:
        def compute() -> _Schedule:
            placements, extended = schedule(
                events, planned.variants, planned.priority, gaps, wps=self.wps
            )
            report = placement_report(placements, planned.variants, events, gaps)
            return _Schedule(placements=placements, extended=extended, report=report)

        with self.step("schedule") as info:
            inputs = {
                "events": events,
                "variants": planned.variants,
                "priority": planned.priority,
                "gaps": gaps,
                "wps": self.wps,
            }
            out = self.cached(info, "schedule", inputs, _Schedule, compute)
            info.update(
                placed=len(out.placements),
                extended=list(out.extended),
                utilisation=out.report.get("utilisation"),
            )
        return out

    def finalize(self, cues, gaps, events, words, cast, names) -> tuple[list[Cue], list[Cue]]:
        by_gap = {g.id: g for g in gaps}
        by_event = {e.id: e for e in events}
        order = sorted((c for c in cues if c.gap_id is not None), key=lambda c: (c.start, c.id))
        order += sorted((c for c in cues if c.gap_id is None), key=lambda c: (c.start, c.id))
        finished: list[Cue] = []
        hits = 0
        with self.step("finalize") as info:
            for cue in order:
                ctx = cue_context(
                    cue,
                    wps=self.wps,
                    duration=self.duration,
                    cast=cast,
                    names=names,
                    words=words,
                    events=events,
                    finished=finished,
                )
                gap = by_gap.get(cue.gap_id) if cue.gap_id is not None else None
                own = {i: by_event[i] for i in cue.event_ids if i in by_event}
                done, hit = self.finalize_one(cue, gap, own, ctx)
                hits += hit
                finished.append(done)
                self.emit(
                    "cue",
                    "finalize",
                    id=done.id,
                    status=done.status,
                    text=done.text,
                    start=done.start,
                    audio_dur=done.audio_dur,
                    note=done.note,
                    cached=hit,
                )
                if done.status == "held":
                    self.warn(
                        "held",
                        f"cue {done.id} held: {done.note}",
                        "finalize",
                        cue_id=done.id,
                        reason=done.note,
                    )
            counts = {s: sum(c.status == s for c in finished) for s in ("verified", "held")}
            info.update(
                cached=bool(order) and hits == len(order),
                cues=len(order),
                **counts,
                extended=sum(c.status == "extended" for c in finished),
            )
        standard = [c for c in finished if c.gap_id is not None]
        return standard, [c for c in finished if c.gap_id is None]

    def finalize_one(
        self, cue: Cue, gap: Gap | None, own: dict[str, VisualEvent], ctx: CueContext
    ) -> tuple[Cue, bool]:
        """One cue finalised, or taken from its own cache entry when its inputs are unchanged
        and its audio is still there."""
        name = f"finalize/{cue.id}"
        key = self.key(
            "finalize",
            {
                "cue": cue,
                "gap": gap,
                "events": own,
                "ctx": ctx,
                "voice": self.opts.voice,
                "language": self.opts.language,
                "profile": self.opts.profile,
            },
        )
        data = self.store.cached(name, key)
        if data is not None:
            try:
                done = Cue.model_validate(data)
            except ValidationError as e:
                log.warning("pipeline: cached %s does not load, finalising again: %s", name, e)
            else:
                if done.audio_path is None or Path(done.audio_path).exists():
                    return done, True
                log.info("pipeline: %s audio is gone, finalising again", cue.id)
        done = finalize_cue(
            cue,
            gap,
            own,
            self.video,
            self.work,
            self.tf,
            self.speech,
            self.opts,
            ctx,
            run_id=self.run_id,
        )
        self.store.save(name, key, done.model_dump(mode="json"))
        return done, False


def run(
    video: Path,
    run_dir: Path,
    opts: RunOptions,
    *,
    tf: TokenFactory,
    speech: SpeechClient,
    tavily: Tavily | None,
    ledger: Ledger,
    on_event: Callable[[Event], None] = _ignore,
    run_id: str | None = None,
    wps: float | None = None,
) -> PublishResult:
    """Describe `video` into `run_dir` (outputs at its top level, stage cache in `stages/`,
    intermediate media in `work/`) and return what was published.

    `run_id` defaults to run_id_for(video); `wps` to the calibrated rate of opts.voice. `tf`
    and `tavily` must record their calls in `ledger` for the call events and the report.
    Raises BudgetExceeded (cap or provider 402), OverlapError, and any stage's own errors,
    with the stages finished so far cached.
    """
    # absolute: the cache keeps audio paths, which must hold from any working directory
    video, run_dir = Path(video), Path(run_dir).absolute()
    emit = _Emitter(on_event)
    t0 = time.monotonic()
    state: _Run | None = None
    try:
        info = media.probe(video)
        sha = file_sha256(video)
        run_id = run_id or run_id_for(video, sha)
        if wps is None:
            wps, source = voice_wps(opts.voice)
            if source != "calibrated":
                emit(
                    "warning",
                    None,
                    kind="voice_uncalibrated",
                    voice=opts.voice,
                    wps=wps,
                    message=f"voice {opts.voice!r} is not calibrated: assuming {wps} words/s "
                    "(run `lull calibrate-voice`)",
                )
        if not wps > 0:
            raise ValueError(f"voice rate must be positive, got {wps}")
        ledger.check(run_id, budget_estimate(info.duration))
        for client in (tf, tavily):
            if client is not None and getattr(client, "ledger", ledger) is not ledger:
                log.warning("pipeline: %s records to another ledger", type(client).__name__)
        state = _Run(
            video, run_dir, opts, run_id, sha, wps, info.duration, tf, speech, tavily, ledger, emit
        )

        def on_call(rec: CallRecord) -> None:
            if rec.run_id == run_id:
                emit(
                    "call",
                    rec.stage,
                    role=rec.role,
                    model=rec.model,
                    provider=rec.provider,
                    tokens_in=rec.tokens_in,
                    tokens_cached=rec.tokens_cached,
                    tokens_out=rec.tokens_out,
                    cost_usd=rec.cost_usd,
                    latency_s=round(rec.latency_s, 3),
                )

        unsubscribe = ledger.subscribe(on_call)
        try:
            result = _stages(state)
        finally:
            unsubscribe()
    except Exception as e:
        emit("error", state.stage if state else None, kind=type(e).__name__, message=str(e))
        raise
    summary = json.loads(result.manifest.read_bytes().decode("utf-8"))["summary"]
    emit(
        "done",
        None,
        run_id=run_id,
        output=str(run_dir),
        manifest=str(result.manifest),
        seconds=round(time.monotonic() - t0, 3),
        summary=summary,
    )
    return result


def _stages(r: _Run) -> PublishResult:
    heard, gaps = r.listen()
    events = r.watch(heard.words)
    found = r.research(events, heard.words)
    planned = r.plan(events, heard.words, gaps, found)
    placed = r.schedule(events, planned, gaps)
    names = set(planned.cast) | {g.canonical for g in found.glossary if g.canonical.strip()}
    cues = compose_cues(
        placed.placements,
        planned.variants,
        events,
        gaps,
        extended=placed.extended,
        proper_nouns=names,
    )
    voiced_later = {i for c in cues if c.gap_id is None for i in c.event_ids}
    missing = [eid for eid in placed.extended if eid not in voiced_later]
    if missing:  # salient, placed nowhere, and no variant for the extended track either
        one = len(missing) == 1
        r.warn(
            "undescribed",
            f"{len(missing)} salient event{'' if one else 's'} {'has' if one else 'have'} no "
            f"description from the plan and stay{'s' if one else ''} undescribed: "
            + ", ".join(missing),
            "schedule",
            event_ids=missing,
        )
    standard, extended = r.finalize(cues, gaps, events, heard.words, planned.cast, names)
    with r.step("publish") as info:
        assert_no_overlap(standard, heard.speech)
        result = publish(
            r.run_dir,
            r.video,
            standard,
            extended,
            events,
            gaps,
            found,
            r.ledger,
            r.run_id,
            r.opts,
            speech=heard.speech,
        )
        info["files"] = len(result.files) + 1
    return result
