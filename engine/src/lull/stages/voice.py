"""Voice: speak a description with the TTS voice and measure the audio it really produced.

Timing is never estimated from a word count once the line is voiced: the measured duration of
the WAV decides whether the cue fits its pause.
"""

from __future__ import annotations

from pathlib import Path

from lull import media
from lull.media import MediaError, audio_duration
from lull.speech import SpeechClient

MAX_TEMPO = 1.10  # faster than this sounds rushed


def voice(text: str, voice: str, speech: SpeechClient, dst: Path) -> tuple[Path, float]:
    """`text` spoken with `voice` into `dst`, and its measured duration in seconds."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    path = Path(speech.synthesize(text, voice, dst))
    dur = audio_duration(path)
    if not dur > 0:
        raise MediaError(f"{path.name}: the voice produced no audio")
    return path, dur


def atempo(src: Path, dst: Path, factor: float) -> Path:
    """`src` played `factor` times faster (pitch kept) into `dst`; 1.0 <= factor <= 1.10."""
    if not 1.0 <= factor <= MAX_TEMPO:
        raise ValueError(f"tempo factor {factor} outside 1.0..{MAX_TEMPO}")
    return media.atempo(Path(src), Path(dst), factor)
