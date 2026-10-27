"""Voice rates: the words per second each TTS voice really speaks, measured once.

Gap capacities, the scheduler's sentence deadlines and the split of long gaps all need the
voice's rate before anything is voiced. `lull calibrate-voice` speaks a fixed 40-word paragraph,
measures the audio and stores the rate in `<LULL_HOME>/voices.json` ({voice: words per second}).
Until a voice is calibrated, rates measured on the local Magpie server are used.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
from pathlib import Path

from pydantic import BaseModel

from lull.config import get_settings
from lull.media import MediaError
from lull.speech import SpeechClient
from lull.stages.plan import count_words
from lull.stages.voice import voice

REFERENCE = (
    "A person in a grey coat walks along a harbour at dawn. They stop beside a red fishing "
    "boat, look out over the water, and smile. Gulls circle above the masts while a crew "
    "unloads wooden crates onto the pier."
)
DEFAULT_WPS = {"Sofia": 3.05}  # calibrated on the local Magpie server, 2026-10-27
FALLBACK_WPS = 2.8  # any other voice until it is calibrated
MIN_WPS, MAX_WPS = 1.0, 6.0  # a plausible speaking rate; anything else is a broken measurement

log = logging.getLogger(__name__)


class CalibrationError(ValueError):
    """The measured rate is not a plausible speaking rate; nothing was stored."""


class Calibration(BaseModel):
    voice: str
    words: int
    seconds: float
    wps: float


def voices_path() -> Path:
    return get_settings().lull_home / "voices.json"


def plausible(wps: object) -> bool:
    """A number of words per second a voice can really speak (MIN_WPS..MAX_WPS)."""
    ok = isinstance(wps, int | float) and not isinstance(wps, bool)
    return ok and math.isfinite(wps) and MIN_WPS <= wps <= MAX_WPS


def _load(path: Path) -> dict[str, float]:
    """The stored rates. A file that is not a JSON object raises; an entry that is not a
    plausible rate is skipped with a warning, so one bad voice never breaks the others."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read {path}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected an object of voice name to words per second")
    rates: dict[str, float] = {}
    for name, wps in data.items():
        if not plausible(wps):
            log.warning("voices: %s: voice %r has an invalid rate %r; ignored", path, name, wps)
            continue
        rates[str(name)] = float(wps)
    return rates


def voice_wps(name: str, path: Path | None = None) -> tuple[float, str]:
    """(words per second, source) for voice `name`; source is "calibrated" (voices.json),
    "default" (a rate measured on the development server) or "fallback" (any other voice)."""
    rates = _load(path or voices_path())
    if name in rates:
        return rates[name], "calibrated"
    if name in DEFAULT_WPS:
        return DEFAULT_WPS[name], "default"
    return FALLBACK_WPS, "fallback"


def calibrate(speech: SpeechClient, name: str, work: Path, path: Path | None = None) -> Calibration:
    """Speak REFERENCE with voice `name`, measure it and store the rate in voices.json.
    A rate outside MIN_WPS..MAX_WPS raises CalibrationError and stores nothing."""
    path = path or voices_path()
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", name) or "voice"
    try:
        _, seconds = voice(REFERENCE, name, speech, Path(work) / f"calibrate-{safe}.wav")
    except MediaError as e:
        raise MediaError(f"the voice {name!r} produced no usable audio: {e}") from e
    words = count_words(REFERENCE)
    cal = Calibration(voice=name, words=words, seconds=seconds, wps=round(words / seconds, 3))
    if not plausible(cal.wps):
        raise CalibrationError(
            f"the voice {name!r} read {words} words in {seconds:.2f} s = {cal.wps:.3f} words/s, "
            f"outside {MIN_WPS:g}-{MAX_WPS:g}: not saved (is the speech server answering with "
            "the reference text?)"
        )
    rates = _load(path)
    rates[name] = cal.wps
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(dict(sorted(rates.items())), indent=2) + "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".voices-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return cal
