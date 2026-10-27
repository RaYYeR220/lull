"""Keeping local paths and configured secrets out of what visitors see.

In order: configured secrets become ***; real URLs (a scheme and a host: https://host/...) are
set aside untouched; file:// prefixes are dropped; every form of every known root is cut,
whatever comes before it (C:\\..., C:/..., /c/..., \\\\?\\C:\\..., file:///C:/..., any case);
any other absolute path shrinks to its last part; any root string still left is removed; the
URLs go back.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from lull.config import get_settings

# a URL with a host: what follows "://" is not a "/" (file:///C:/... is a local path)
URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/\\'\"<>()\x00]+[^\s'\"<>\x00]*")
_FILE_DRIVE = re.compile(r"\bfile:///(?=[A-Za-z]:)", re.IGNORECASE)
_FILE_ROOT = re.compile(r"\bfile://(?=/)", re.IGNORECASE)
# A root ends at a separator or where the path does: /data is not the start of /database.
_ROOT_END = r"(?:[\\/]|(?=[\s'\"),;:\x00]|\Z))"
# An absolute path (optionally \\?\ or //?/ prefixed) not glued to a word, a "." or a slash
# before it (so not 24/7/365); it is replaced by its last part.
ABS_PATH = re.compile(
    r"(?<![\w/\\.])(?:\\\\\?\\|//\?/)?(?:[A-Za-z]:[\\/]|/)"
    r"(?:[^\s'\"\\/:\x00]+[\\/])+(?P<name>[^\s'\"\\/:,;)\x00]*)"
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


def root_forms(root: str) -> set[str]:
    """The ways a root folder can be written: as given, with either slash, and for a drive
    path also MSYS (/c/...), extended-length (\\\\?\\C:\\...) and file:///C:/... forms."""
    native = root.rstrip("/\\")
    posix = native.replace("\\", "/")
    forms = {native, posix}
    drive = re.match(r"([A-Za-z]):/(.*)", posix)
    if drive:
        letter, rest = drive.groups()
        back = posix.replace("/", "\\")
        forms |= {back, f"/{letter}/{rest}", "\\\\?\\" + back, "//?/" + posix}
        forms.add("file:///" + posix)
    elif posix.startswith("/"):
        forms.add("file://" + posix)
    return {f for f in forms if len(f.strip("/\\")) > 1}


class Scrubber:
    """Takes local paths and configured secrets out of text meant for visitors: the run
    folder and other known roots are cut off (input/clip.mp4 stays), any other absolute path
    shrinks to its file name. With `names_only`, a path under a root shrinks to its file name
    too. Text inside a URL is never touched."""

    def __init__(self, run_dir: Path, roots: Iterable[Path], *, names_only: bool = False) -> None:
        forms: set[str] = set()
        for root in (run_dir, *roots):
            forms |= root_forms(str(root))
        ordered = sorted(forms, key=len, reverse=True)
        alternatives = "|".join(re.escape(f) for f in ordered)
        self._roots = re.compile(f"(?:{alternatives}){_ROOT_END}", re.IGNORECASE)
        self._leftover = re.compile(alternatives, re.IGNORECASE)
        # a stand-in absolute prefix, so ABS_PATH then takes what follows down to its name
        self._cut = "/_/" if names_only else ""
        s = get_settings()
        keys = (s.nebius_api_key, s.tavily_api_key, s.lull_speech_key)
        self._secrets = [k for k in keys if k and len(k) >= 8]

    def __call__(self, text: str) -> str:
        for secret in self._secrets:  # first: a key may sit in a URL's query
            text = text.replace(secret, "***")
        urls: list[str] = []

        def park(m: re.Match) -> str:
            urls.append(m.group(0))
            return f"\x00{len(urls) - 1}\x00"

        text = URL.sub(park, text.replace("\x00", ""))
        text = _FILE_ROOT.sub("", _FILE_DRIVE.sub("", text))
        text = self._roots.sub(self._cut, text)
        text = ABS_PATH.sub(lambda m: m.group("name"), text)
        text = self._leftover.sub("", text)
        return re.sub("\x00(\\d+)\x00", lambda m: urls[int(m.group(1))], text)

    def tree(self, obj: Any) -> Any:
        """`obj` (JSON data) with every string scrubbed."""
        if isinstance(obj, str):
            return self(obj)
        if isinstance(obj, dict):
            return {k: self.tree(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.tree(v) for v in obj]
        return obj
