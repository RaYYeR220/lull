"""Keeping local paths and configured secrets out of what visitors see."""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from lull.config import get_settings

# A path starts where nothing word-, path- or URL-like comes before it: not after a letter,
# a ":" or a "/", so never inside https://host/data/...
_START = r"(?<![\w:/.\\-])"
# A root ends at a separator or where the path does: /data is not the start of /database.
_ROOT_END = r"(?:[\\/]|(?=[\s'\"),;:]|\Z))"
# an absolute path not inside a URL; it is replaced by its last part
ABS_PATH = re.compile(
    _START + r"(?:[A-Za-z]:[\\/]|/)(?:[^\s'\"\\/:]+[\\/])+(?P<name>[^\s'\"\\/:,;)]*)"
)


def default_roots(data_dir: Path) -> tuple[Path, ...]:
    """The folders whose names should never reach a visitor: LULL_DATA, LULL_HOME, the home
    and temp folders."""
    return (
        data_dir.absolute(),
        get_settings().lull_home.absolute(),
        Path.home(),
        Path(tempfile.gettempdir()),
    )


class Scrubber:
    """Takes local paths and configured secrets out of text meant for visitors: the run
    folder and other known roots are cut off (input/clip.mp4 stays), any other absolute path
    shrinks to its file name. With `names_only`, a path under a root shrinks to its file name
    too. A root matches only as whole path parts at the start of a path, never inside a URL."""

    def __init__(self, run_dir: Path, roots: Iterable[Path], *, names_only: bool = False) -> None:
        forms = set()
        for root in (run_dir, *roots):
            for form in (str(root), root.as_posix()):
                form = form.rstrip("/\\")
                if len(form) > 1:
                    forms.add(form)
        ordered = sorted(forms, key=len, reverse=True)
        alternatives = "|".join(re.escape(f) for f in ordered)
        self._roots = re.compile(f"{_START}(?:{alternatives}){_ROOT_END}", re.IGNORECASE)
        # a stand-in absolute prefix, so ABS_PATH then takes what follows down to its name
        self._cut = "/_/" if names_only else ""
        s = get_settings()
        keys = (s.nebius_api_key, s.tavily_api_key, s.lull_speech_key)
        self._secrets = [k for k in keys if k and len(k) >= 8]

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        text = self._roots.sub(self._cut, text)
        return ABS_PATH.sub(lambda m: m.group("name"), text)

    def tree(self, obj: Any) -> Any:
        """`obj` (JSON data) with every string scrubbed."""
        if isinstance(obj, str):
            return self(obj)
        if isinstance(obj, dict):
            return {k: self.tree(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.tree(v) for v in obj]
        return obj
