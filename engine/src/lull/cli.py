"""Command line: `lull describe`, `lull calibrate-voice`, `lull ledger` and
`lull export-showcase`.

Settings come from the environment or a .env file: NEBIUS_API_KEY, TAVILY_API_KEY (optional),
LULL_SPEECH_URL (default http://127.0.0.1:8089), LULL_SPEECH_KEY, LULL_HOME (ledger and voice
rates, default ~/.lull), the LULL_CAP_RUN / LULL_CAP_DAY / LULL_CAP_TOTAL model spend caps in
USD and the LULL_TAVILY_RUN / LULL_TAVILY_DAY web search caps (a count). A cap of 0 refuses
everything; "none" switches it off.
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from lull.config import get_settings
from lull.ledger import BudgetExceeded, Ledger
from lull.media import MediaError
from lull.pipeline import Event, run, run_id_for
from lull.showcase import ShowcaseError, export_showcase
from lull.speech import DEFAULT_URL, NemoSpeechHTTP, SpeechClient, SpeechError
from lull.stages.finalize import OverlapError
from lull.stages.watch import PROFILES
from lull.store import file_sha256
from lull.tavily import Tavily
from lull.tf import LIGHTNING, ULTRA, VL, TFError, TokenFactory
from lull.types import RunOptions
from lull.voices import CalibrationError, calibrate, voices_path

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Lull: audio description that fits the silences between dialogue.",
)

MODEL_NAMES = {ULTRA: "Ultra", VL: "Super VL", LIGHTNING: "Lightning"}
RUNS_DIR = "runs"
LOG_FILE = "run.log"


class Mode(StrEnum):
    standard = "standard"
    extended = "extended"


Profile = StrEnum("Profile", {p: p for p in PROFILES})  # the content profiles the prompts know


@app.callback()
def _main() -> None:
    # A description may hold characters the console cannot show; never crash on printing them.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


# -- clients -----------------------------------------------------------------------------------


def _ledger() -> Ledger:
    return Ledger(get_settings().lull_home / "ledger.db")


def _speech() -> SpeechClient:
    s = get_settings()
    speech = NemoSpeechHTTP.from_settings()
    if not speech.health():
        url = s.lull_speech_url or DEFAULT_URL
        _fail(f"the speech server at {url} is not answering (start `nemo-speech serve`)", 1)
    return speech


def _clients(no_research: bool) -> tuple[Ledger, TokenFactory, SpeechClient, Tavily | None]:
    ledger = _ledger()
    try:
        tf = TokenFactory.from_settings(ledger)
    except TFError as e:
        _fail(str(e), 1)
    speech = _speech()
    tavily = None if no_research else Tavily.from_settings(ledger)
    return ledger, tf, speech, tavily


def _one_line(text: str) -> str:
    return " ".join(text.split())[:500]


def _fail(message: str, code: int) -> None:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code)


# -- progress ----------------------------------------------------------------------------------


def _model(name: str) -> str:
    return MODEL_NAMES.get(name, name)


def _clock(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:04.1f}"


def _detail(stage: str, d: dict) -> str:
    if stage == "listen":
        text = (
            f"{d['words']} words, {d['gaps']} gaps -> {d['windows']} windows "
            f"({d['capacity_words']} words of room)"
        )
        return text + (f", {d['clamped']} stretched words clamped" if d["clamped"] else "")
    if stage == "watch":
        return f"{d['events']} visual events"
    if stage == "research":
        text = f"{d['terms']} terms" + (" (not looked up)" if d["skipped"] else "")
        return text + (f", {d['errors']} lookup errors" if d["errors"] else "")
    if stage == "plan":
        cast = f", cast: {', '.join(d['cast'])}" if d["cast"] else ""
        return f"{d['variants']} variants for {d['events']} events{cast}"
    if stage == "schedule":
        used = d.get("utilisation") or 0.0
        return (
            f"{d['placed']} placed, {len(d['extended'])} salient events left for the extended "
            "track, "
            f"{used:.0%} of the room used"
        )
    if stage == "finalize":
        return f"{d['verified']} verified, {d['held']} held, {d['extended']} extended"
    if stage == "publish":
        return f"{d['files']} files"
    return ""


class _Printer:
    """Prints pipeline events as they come."""

    def __init__(self) -> None:
        self.summary: dict | None = None
        self.seconds = 0.0

    def __call__(self, e: Event) -> None:
        d = e.data
        if e.type == "stage_start":
            typer.echo(f"{e.stage} ...")
        elif e.type == "stage_end":
            cached = "cached, " if d.get("cached") else ""
            typer.echo(f"{e.stage:<9}{d['seconds']:>7.1f} s  {cached}{_detail(e.stage, d)}")
        elif e.type == "call":
            typer.echo(
                f"    call  {d['role']:<9} {_model(d['model']):<13} {d['tokens_in']:>8,} in "
                f"{d['tokens_out']:>7,} out  ${d['cost_usd']:.4f}  {d['latency_s']:.1f} s"
            )
        elif e.type == "cue":
            dur = f"{d['audio_dur']:.1f} s" if d.get("audio_dur") else "-"
            typer.echo(
                f"    cue   {d['id']:<5} {d['status']:<9} {_clock(d['start'])}  "
                f"{dur:>6}  {d['text']}"
            )
        elif e.type == "warning":
            typer.echo(f"  ! {d['message']}")
        elif e.type == "done":
            self.summary, self.seconds = d["summary"], d["seconds"]


def _print_summary(summary: dict, seconds: float, out: Path) -> None:
    cues = summary["cues"]
    typer.echo(f"done in {seconds:.1f} s")
    typer.echo(
        f"  cues      {cues['verified']} verified, {cues['held']} held, {cues['extended']} extended"
    )
    for h in summary.get("held", []):
        typer.echo(f"  held      {h['id']}: {h['reason']}")
    typer.echo(f"  overlap   {summary['overlap_seconds']:.3f} s")
    typer.echo(f"  model spend ${summary['cost_usd']:.4f} this run")
    typer.echo(f"  output    {out}")


@contextmanager
def _log_to(path: Path) -> Iterator[None]:
    """The engine's log goes to `path` while the command runs, not to the console."""
    logger = logging.getLogger("lull")
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    level, propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(level)
        logger.propagate = propagate


# -- commands ----------------------------------------------------------------------------------


@app.command()
def describe(
    video: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, help="The video to describe."),
    ],
    out: Annotated[
        Path | None,
        typer.Option(help="Output folder (default: runs/<run id> under the current folder)."),
    ] = None,
    mode: Annotated[
        Mode,
        typer.Option(
            help="standard, or extended: the plan writes full long versions for the extended "
            "track. Both modes also build extended.mp4 when a salient visual fits no pause."
        ),
    ] = Mode.standard,
    profile: Annotated[
        Profile,
        typer.Option(
            help="What matters most: education (slides, charts, on-screen text) or "
            "entertainment (action, people, setting)."
        ),  # fmt: skip
    ] = Profile.education,
    voice: Annotated[str, typer.Option(help="Magpie TTS voice.")] = "Sofia",
    source_url: Annotated[
        str | None, typer.Option(help="A page about the video, read for names and terms.")
    ] = None,
    no_research: Annotated[
        bool, typer.Option("--no-research", help="Do not look names and terms up on the web.")
    ] = False,
    run_id: Annotated[
        str | None,
        typer.Option(help="Run id for the ledger and cache (default: <video stem>-<sha256[:8]>)."),
    ] = None,
) -> None:
    """Describe VIDEO: audio description fitted to its pauses, verified against its frames.
    Descriptions are written and voiced in English only.

    Exit codes: 0 published; 1 an error (one line, details in the run log); 2 bad usage;
    3 stopped by a spend cap or the provider's balance; 4 nothing published (overlap)."""
    sha = file_sha256(video)  # once: the run gets it too
    rid = run_id or run_id_for(video, sha)
    out = out or Path(RUNS_DIR) / rid
    opts = RunOptions(mode=mode.value, profile=profile.value, voice=voice, source_url=source_url)
    ledger, tf, speech, tavily = _clients(no_research)
    printer = _Printer()
    log_path = out / LOG_FILE
    typer.echo(f"describing {video.name} (run {rid}) into {out}")
    try:
        with _log_to(log_path):
            try:
                run(
                    video,
                    out,
                    opts,
                    tf=tf,
                    speech=speech,
                    tavily=tavily,
                    ledger=ledger,
                    on_event=printer,
                    run_id=rid,
                    sha256=sha,
                )
            except Exception as e:  # the traceback goes to the run log, not the console
                if not isinstance(e, BudgetExceeded | OverlapError):
                    logging.getLogger("lull").error("describe failed", exc_info=e)
                raise
    except BudgetExceeded as e:
        resume = "finished stages are cached: the same command resumes where it stopped"
        if e.scope == "provider":
            _fail(f"Token Factory refused the call for lack of funds ({e}); top up, {resume}", 3)
        knob = {"run": "LULL_CAP_RUN", "day": "LULL_CAP_DAY", "total": "LULL_CAP_TOTAL"}[e.scope]
        hint = f"raise {knob}"
        if e.scope == "run":
            hint += " or start a fresh ledger entry with another --run-id"
        _fail(f"stopped by the {e.scope} spend cap: {e}; {hint}; {resume}", 3)
    except OverlapError as e:
        _fail(f"nothing published: {e}", 4)
    except Exception as e:  # one line for the person
        _fail(f"{type(e).__name__}: {_one_line(str(e))} (log: {log_path})", 1)
    finally:
        for client in (tf, tavily):
            close = getattr(client, "close", None)
            if close is not None:
                close()
    if printer.summary is not None:
        _print_summary(printer.summary, printer.seconds, out)


@app.command("mcp")
def mcp_cmd() -> None:
    """Serve Lull to an MCP client (Claude Desktop, any agent) over stdio, on this machine with
    your own keys and spend caps: describe a local video file, fetch its tracks and report,
    list the runs with lines held for review. Runs go under LULL_HOME/runs."""
    from lull.api.agents import local_server

    local_server().run("stdio")


@app.command("calibrate-voice")
def calibrate_voice(
    voice: Annotated[str, typer.Option(help="Magpie TTS voice to measure.")] = "Sofia",
) -> None:
    """Speak a 40-word reference paragraph, measure words per second and store it."""
    speech = _speech()
    with tempfile.TemporaryDirectory(prefix="lull-calibrate-") as work:
        try:
            cal = calibrate(speech, voice, Path(work))
        except (SpeechError, MediaError, CalibrationError) as e:
            _fail(f"{type(e).__name__}: {e}", 1)
    typer.echo(
        f"{cal.voice}: {cal.words} words in {cal.seconds:.2f} s = {cal.wps:.3f} words/s "
        f"(saved to {voices_path()})"
    )


def _cap(cap: float | None, *, money: bool = True) -> str:
    if cap is None:
        return "no cap"
    return f"cap ${cap:.2f}" if money else f"cap {cap}"


@app.command("ledger")
def ledger_cmd(
    run_id: Annotated[str | None, typer.Option("--run", help="Show one run's calls.")] = None,
) -> None:
    """Model spend and web searches: every run, or one run by role and model."""
    led = _ledger()
    if run_id is None:
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        today, total = led.spent(since=midnight), led.spent()
        typer.echo(
            f"model     today ${today:.4f} ({_cap(led.cap_day)}), "
            f"total ${total:.4f} ({_cap(led.cap_total)})"
        )
        typer.echo(
            f"searches  today {led.searches(since=midnight)} ({_cap(led.tavily_day, money=False)})"
        )
        for r in led.runs():
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_ts"]))
            searched = f", {r['searches']} searches" if r["searches"] else ""
            typer.echo(
                f"  {r['run_id']:<32} {r['calls']:>4} calls  ${r['cost_usd']:.4f}  {when}{searched}"
            )
        return
    summary = led.summary(run_id)
    if not summary["calls"]:
        _fail(f"no calls recorded for run {run_id!r}", 1)
    typer.echo(f"run {run_id}")
    typer.echo(
        f"  {'role':<9} {'model':<13} {'calls':>5} {'tokens in':>10} {'cached':>8} "
        f"{'out':>8}  {'cost':>8}  {'seconds':>7}"
    )
    rows = summary["by_role_model"]
    for g in rows:
        typer.echo(
            f"  {g['role']:<9} {_model(g['model']):<13} {g['calls']:>5} "
            f"{g['tokens_in']:>10,} {g['tokens_cached']:>8,} {g['tokens_out']:>8,}  "
            f"${g['cost_usd']:.4f}  {g['latency_s']:>7.1f}"
        )
    tin = sum(g["tokens_in"] for g in rows)
    cached = sum(g["tokens_cached"] for g in rows)
    tout = sum(g["tokens_out"] for g in rows)
    seconds = sum(g["latency_s"] for g in rows)
    typer.echo(
        f"  {'total':<9} {'':<13} {summary['calls']:>5} {tin:>10,} {cached:>8,} "
        f"{tout:>8,}  ${summary['model_cost_usd']:.4f}  {seconds:>7.1f}  (model spend)"
    )
    if summary["searches"]:
        listed = summary["total_cost_usd"] - summary["model_cost_usd"]
        typer.echo(
            f"  {'searches':<9} {summary['searches']} web searches, counted, not charged "
            f"(${listed:.4f} at list price)"
        )


@app.command("export-showcase")
def export_showcase_cmd(
    run_dir: Annotated[Path, typer.Argument(help="A finished `lull describe` output folder.")],
    out_dir: Annotated[Path, typer.Argument(help="Where the website's files go.")],
    id: Annotated[str | None, typer.Option("--id", help="Showcase id (default: folder name).")] = (
        None
    ),
    title: Annotated[str | None, typer.Option(help="Title (default: the video's name).")] = None,
    credit: Annotated[str, typer.Option(help="Credit line for the video.")] = "",
) -> None:
    """Export a finished run for the website: showcase.json, a small video.mp4 with the
    original sound, the described audio, the description tracks, each cue's audio and the
    keyframes.

    costUsd is the model spend of the attempt that published the run; showcase.json's
    summary.spend gives it beside the model spend of every run under the run id and the web
    searches."""
    try:
        path = export_showcase(run_dir, out_dir, id=id, title=title, credit=credit)
    except (ShowcaseError, MediaError) as e:
        _fail(f"{type(e).__name__}: {_one_line(str(e))}", 1)
    data = json.loads(path.read_text(encoding="utf-8"))
    files = sum(1 for p in Path(out_dir).rglob("*") if p.is_file())
    typer.echo(
        f"{data['id']}: {len(data['cues'])} cues, {len(data['gaps'])} pauses, "
        f"{data['duration']:.1f} s, ${data['costUsd']:.4f}; {files} files in {out_dir} "
        f"({path.name})"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
