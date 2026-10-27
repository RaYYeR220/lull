"""Voice rates: the words per second each TTS voice really speaks, measured once.

Gap capacities, the scheduler's sentence deadlines and the split of long gaps all need the
voice's rate before anything is voiced. `lull calibrate-voice` speaks a fixed 40-word paragraph,
measures the audio and stores the rate in `<LULL_HOME>/voices.json` ({voice: words per second}).
Until a voice is calibrated, rates measured on the local Magpie server are used.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path

from pydantic import BaseModel

from lull.config import get_settings
from lull.speech import SpeechClient
from lull.stages.plan import count_words
from lull.stages.voice import voice

REFERENCE = (
    "A person in a grey coat walks along a harbour at dawn. They stop beside a red fishing "
    "boat, look out over the water, and smile. Gulls circle above the masts while a crew "
    "unloads wooden crates onto the pier."
)
DEFAULT_WPS = {"Sofia": 2.36, "Aria": 2.06}  # measured on Magpie, 2026-10-27
FALLBACK_WPS = min(DEFAULT_WPS.values())  # an unknown voice: the slowest known rate is safest
MAX_WPS = 20.0


class Calibration(BaseModel):
    voice: str
    words: int
    seconds: float
    wps: float


def voices_path() -> Path:
    return get_settings().lull_home / "voices.json"


def _load(path: Path) -> dict[str, float]:
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
        ok = isinstance(wps, int | float) and not isinstance(wps, bool)
        if not (ok and math.isfinite(wps) and 0 < wps < MAX_WPS):
            raise ValueError(f"{path}: voice {name!r} has an invalid rate {wps!r}")
        rates[str(name)] = float(wps)
    return rates


def voice_wps(name: str, path: Path | None = None) -> tuple[float, str]:
    """(words per second, source) for voice `name`; source is "calibrated" (voices.json),
    "default" (a measured built-in rate) or "fallback" (unknown voice, slowest known rate)."""
    rates = _load(path or voices_path())
    if name in rates:
        return rates[name], "calibrated"
    if name in DEFAULT_WPS:
        return DEFAULT_WPS[name], "default"
    return FALLBACK_WPS, "fallback"


def calibrate(speech: SpeechClient, name: str, work: Path, path: Path | None = None) -> Calibration:
    """Speak REFERENCE with voice `name`, measure it and store the rate in voices.json."""
    path = path or voices_path()
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", name) or "voice"
    _, seconds = voice(REFERENCE, name, speech, Path(work) / f"calibrate-{safe}.wav")
    words = count_words(REFERENCE)
    cal = Calibration(voice=name, words=words, seconds=seconds, wps=round(words / seconds, 3))
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
