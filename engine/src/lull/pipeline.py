"""The pipeline: every stage in order, each cached, spend checked first, progress as events.

    probe + container check -> spend check -> listen -> find_gaps + split_gaps -> watch
    -> research -> plan -> schedule + compose_cues -> finalize (standard cues in time order,
    then extended cues) -> assert_no_overlap -> publish (mix included)

One voice rate (words per second, calibrated per voice; see lull.voices) times everything: the
gap capacities, the windows split_gaps cuts and the scheduler's sentence deadlines. Each
stage's result is cached in the run directory (lull.store) under a key over its inputs, the
video's sha256 and the engine fingerprint (package version + every prompt file), finalize cue
by cue; a run stopped by a spend cap or a crash resumes where it stopped, a finished run repeats
without a model call, and the run directory can move. Before anything runs the ledger must allow
an estimate of $0.05 a minute + $0.02; a cap or a provider 402 mid-run raises BudgetExceeded
with every finished stage kept.

Progress goes to `on_event` as Event(type, stage, data, ts): stage_start / stage_end (seconds,
cached, counts), a `call` per ledger record, a `cue` per finalised cue, `warning`s (held cues,
clamped words, research problems, an incomplete plan, undescribed events, an uncalibrated
voice), `error` (then the exception propagates) and `done` with the published summary. Events
are delivered one at a time under a re-entrant lock: calls recorded on other threads wait their
turn, and an event the callback itself causes is delivered from inside it. A callback that
raises is logged and never stops a run.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib.resources import files
from importlib.resources.abc import Traversable
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
from lull.timeline import find_gaps, split_gaps
from lull.types import Cue, Gap, RunOptions, Span, VisualEvent, Word
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


def prompts_digest(root: Path | Traversable | None = None) -> str:
    """sha256 over the name and contents of every prompt file (.md) in `root` (default: the
    packaged prompts), in name order."""
    root = root if root is not None else files("lull") / "prompts"
    h = hashlib.sha256()
    for item in sorted((i for i in root.iterdir() if i.name.endswith(".md")), key=lambda i: i.name):
        h.update(item.name.encode("utf-8") + b"\0" + item.read_bytes() + b"\0")
    return h.hexdigest()


@functools.cache
def engine_fingerprint() -> str:
    """What every stage key includes besides its inputs: the package version and the prompts,
    so a new engine or an edited prompt never reuses an old stage result."""
    return f"{lull.__version__}+{prompts_digest()[:16]}"


# -- who has been introduced -------------------------------------------------------------------


def introduced_before(
    cutoff: float, names: Iterable[str], words: list[Word], heard: Iterable[Cue]
) -> set[str]:
    """The names (whole words, case-sensitive) a listener has met by `cutoff`: said in a
    transcript word that ends before it, or read in a voiced description that starts before it.
    On-screen text alone introduces nobody (strict I4): only a description that reads it does."""
    names = {n for n in names if n}
    texts = [" ".join(w.text for w in words if w.end < cutoff)]
    texts += [c.text for c in heard if c.start < cutoff]
    return set().union(*(names_in(t, names) for t in texts))


def cutoff_for(cue: Cue, speech: list[Span]) -> float:
    """When a listener reaches `cue`: a standard cue's start; for an extended cue the moment the
    picture freezes for it, which waits for speech under its visual (mix.speech_free)."""
    return cue.start if cue.gap_id is not None else mixer.speech_free(cue.start, speech)


def heard_before(cue: Cue, finished: Iterable[Cue], cutoff: float) -> list[Cue]:
    """The finished cues a listener has heard by `cutoff`: voiced standard cues, and for an
    extended cue the voiced extended cues as well (the extended version has both)."""
    finished = list(finished)
    heard = mixer.voiced(finished)
    if cue.gap_id is None:
        heard += mixer.voiced_extended(finished)
    return [c for c in heard if c.start < cutoff]


def cue_context(
    cue: Cue,
    *,
    wps: float,
    duration: float,
    cast: dict[str, str],
    named: set[str],
    proper_nouns: set[str],
    words: list[Word],
    events: list[VisualEvent],
    speech: list[Span],
    finished: list[Cue],
) -> CueContext:
    """What finalising `cue` needs to know about the rest of the programme. `named` are the
    names lint checks for introduction (cast and glossary people); `proper_nouns` every
    spelling to keep (cast and the whole glossary)."""
    by_id = {e.id: e for e in events}
    own = sorted((by_id[i] for i in cue.event_ids if i in by_id), key=lambda e: (e.t_start, e.id))
    cutoff = cutoff_for(cue, speech)
    heard = heard_before(cue, finished, cutoff)
    return CueContext(
        wps=wps,
        cast=dict(cast),
        named=set(named),
        introduced=introduced_before(cutoff, named, words, heard),
        verbatim=[e.verbatim_text for e in own if e.verbatim_text],
        proper_nouns=set(proper_nouns),
        video_duration=duration,
    )


# -- the run -----------------------------------------------------------------------------------


class _Emitter:
    """Builds events and hands them to the callback one at a time (re-entrant: an event the
    callback causes on its own thread is delivered from inside it)."""

    def __init__(self, on_event: Callable[[Event], None]) -> None:
        self._on_event = on_event
        self._lock = threading.RLock()

    def __call__(self, type_: EventType, stage: str | None, **data) -> None:
        event = Event(type=type_, stage=stage, data=data, ts=time.time())
        with self._lock:
            try:
                self._on_event(event)
            except Exception:
                log.exception("pipeline: the event callback failed on a %s event", type_)


def _ignore(event: Event) -> None:
    pass


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


@dataclass
class _Run:
    video: Path
    run_dir: Path  # absolute
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
            {"stage": stage, "engine": engine_fingerprint(), "video": self.sha256, "inputs": inputs}
        )

    @contextmanager
    def step(self, name: str) -> Iterator[dict]:
        """stage_start, then stage_end with its seconds and whatever the body put in the dict.
        When the body raises there is no stage_end and the stage stays current, so the run's
        error event names it."""
        self.stage = name
        self.emit("stage_start", name)
        info: dict = {"cached": False}
        t0 = time.monotonic()
        yield info
        self.emit("stage_end", name, seconds=round(time.monotonic() - t0, 3), **info)
        self.stage = None

    def cached(
        self,
        info: dict,
        name: str,
        inputs: dict,
        model: type[M],
        compute: Callable[[], M],
        keep: Callable[[M], bool] = lambda result: True,
    ) -> M:
        """The stage result from the store when its inputs are unchanged, else computed and
        stored (unless `keep` says it should be computed again next time)."""
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
        if keep(result):
            self.store.save(name, key, result.model_dump(mode="json"))
        else:
            log.info("pipeline: %s result not cached; the next run computes it again", name)
        return result

    def warn(self, kind: str, message: str, stage: str | None = None, **data) -> None:
        self.emit("warning", stage or self.stage, kind=kind, message=message, **data)

    # -- stages ------------------------------------------------------------------------------

    def listen(self) -> tuple[ListenResult, list[Gap]]:
        """The transcript is cached whatever the voice rate; the gaps and their room are
        computed from it with this run's rate."""
        with self.step("listen") as info:
            heard = self.cached(
                info,
                "listen",
                {},
                ListenResult,
                lambda: listen(self.video, self.work, self.speech, wps=self.wps),
            )
            gaps = find_gaps(heard.speech, heard.duration, protected=heard.protected, wps=self.wps)
            heard = heard.model_copy(update={"gaps": gaps})
            windows = split_gaps(gaps, wps=self.wps)
            info.update(
                words=len(heard.words),
                speakers=len({t.speaker for t in heard.turns}),
                speech_s=round(sum(s.dur for s in heard.speech), 3),
                gaps=len(gaps),
                windows=len(windows),
                capacity_words=sum(g.capacity_words for g in windows),
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
        return heard, windows

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
        """Not cached when a lookup failed: the next run tries again."""
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
                    events, words, self.opts, self.tf, self.tavily, run_id=self.run_id
                ),
                keep=lambda result: not result.errors,
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
                lambda: plan(events, words, gaps, found, self.opts, self.tf, run_id=self.run_id),
            )
            info.update(
                variants=len(planned.variants),
                events=len(planned.priority),
                cast=sorted(planned.cast),
                unanswered=list(planned.unanswered),
            )
        if planned.unanswered:
            self.warn(
                "plan_incomplete",
                f"the plan wrote nothing for {_plural(len(planned.unanswered), 'event')} of "
                "salience 3 or more, even when asked again: " + ", ".join(planned.unanswered),
                "plan",
                event_ids=list(planned.unanswered),
            )
        return planned

    def schedule(
        self, events: list[VisualEvent], planned: PlanResult, gaps: list[Gap], proper: set[str]
    ) -> list[Cue]:
        """Placements (cached) and the cues composed from them."""

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
            cues = compose_cues(
                out.placements,
                planned.variants,
                events,
                gaps,
                extended=out.extended,
                proper_nouns=proper,
            )
            info.update(
                placed=len(out.placements),
                extended=list(out.extended),
                utilisation=out.report.get("utilisation"),
            )
        later = {i for c in cues if c.gap_id is None for i in c.event_ids}
        missing = [eid for eid in out.extended if eid not in later]
        if missing:  # salient, placed nowhere, and no variant for the extended track either
            one = len(missing) == 1
            self.warn(
                "undescribed",
                f"{_plural(len(missing), 'salient event')} {'has' if one else 'have'} no "
                f"description from the plan and stay{'s' if one else ''} undescribed: "
                + ", ".join(missing),
                "schedule",
                event_ids=missing,
            )
        return cues

    def finalize(
        self, cues: list[Cue], gaps: list[Gap], events: list[VisualEvent], heard: ListenResult,
        cast: dict[str, str], named: set[str], proper: set[str],
    ) -> tuple[list[Cue], list[Cue]]:  # fmt: skip
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
                    named=named,
                    proper_nouns=proper,
                    words=heard.words,
                    events=events,
                    speech=heard.speech,
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

    def _relative(self, cue: Cue) -> Cue:
        """The cue with its audio path relative to the run directory (as the cache keeps it)."""
        if cue.audio_path is None:
            return cue
        try:
            rel = os.path.relpath(Path(cue.audio_path).absolute(), self.run_dir)
        except ValueError:  # another drive: it cannot move with the run anyway
            return cue
        return cue.model_copy(update={"audio_path": Path(rel).as_posix()})

    def _absolute(self, cue: Cue) -> Cue:
        if cue.audio_path is None or Path(cue.audio_path).is_absolute():
            return cue
        return cue.model_copy(update={"audio_path": str(self.run_dir / cue.audio_path)})

    def finalize_one(
        self, cue: Cue, gap: Gap | None, own: dict[str, VisualEvent], ctx: CueContext
    ) -> tuple[Cue, bool]:
        """One cue finalised, or taken from its own cache entry when its inputs are unchanged
        and its audio is still there. Its files live in a folder named after its cache key,
        so audio from other inputs can never stand in for it."""
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
                done = self._absolute(Cue.model_validate(data))
            except ValidationError as e:
                log.warning("pipeline: cached %s does not load, finalising again: %s", name, e)
            else:
                if done.audio_path is None or Path(done.audio_path).exists():
                    return done, True
                log.info("pipeline: %s audio is gone, finalising again", cue.id)
        work = self.work / "finalize" / f"{cue.id}-{key[:12]}"
        for old in (self.work / "finalize").glob(f"{cue.id}-*"):
            if old != work:
                shutil.rmtree(old, ignore_errors=True)
        done = finalize_cue(
            cue,
            gap,
            own,
            self.video,
            work,
            self.tf,
            self.speech,
            self.opts,
            ctx,
            run_id=self.run_id,
        )
        self.store.save(name, key, self._relative(done).model_dump(mode="json"))
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
    sha256: str | None = None,
) -> PublishResult:
    """Describe `video` into `run_dir` (outputs at its top level, stage cache in `stages/`,
    intermediate media in `work/`) and return what was published.

    `run_id` defaults to run_id_for(video); `wps` to the calibrated rate of opts.voice;
    `sha256` (the video's, when the caller already hashed it) is computed when not given. `tf`
    and `tavily` must record their calls in `ledger` for the call events and the report.
    Raises BudgetExceeded (cap or provider 402), OverlapError, and any stage's own errors,
    with the stages finished so far cached.
    """
    given = Path(run_dir)
    video, run_dir = Path(video), given.absolute()
    emit = _Emitter(on_event)
    t0 = time.monotonic()
    state: _Run | None = None
    try:
        info = media.probe(video)
        media.assert_safe_container(info)  # before anything else reads it (E-sec)
        sha = sha256 or file_sha256(video)
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
                    message=f"voice {opts.voice!r} is not calibrated here: assuming {wps} "
                    "words/s (run `lull calibrate-voice`)",
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
        output=str(given),
        manifest=Path(os.path.relpath(result.manifest, run_dir)).as_posix(),
        seconds=round(time.monotonic() - t0, 3),
        summary=summary,
    )
    return result


def _stages(r: _Run) -> PublishResult:
    heard, gaps = r.listen()
    events = r.watch(heard.words)
    found = r.research(events, heard.words)
    planned = r.plan(events, heard.words, gaps, found)
    spelled = {g.canonical for g in found.glossary if g.canonical.strip()}
    people = {g.canonical for g in found.glossary if g.kind == "person" and g.canonical.strip()}
    proper = set(planned.cast) | spelled  # spellings to keep
    named = set(planned.cast) | people  # names a listener must be introduced to (R15)
    cues = r.schedule(events, planned, gaps, proper)
    standard, extended = r.finalize(cues, gaps, events, heard, planned.cast, named, proper)
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
