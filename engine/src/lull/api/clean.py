"""Keeping local paths and configured secrets out of what visitors see.

In order: configured secrets become ***; the scheme://host[:port] of each network URL is set
aside (a file: URL, or one whose "host" is a drive letter, is a local path and is not);
file://host prefixes are dropped; every form of every known root is cut wherever it starts
(C:\\..., C:/..., /C:/..., /c/..., \\\\?\\C:\\..., any case); any other absolute path outside a
URL shrinks to its last part; the set-aside parts go back; and a final pass over the whole
text removes any root still left, so none survives, in a URL's path or query included.

A root is cut only as whole path parts: /data is cut from /data/x but not from /datasets.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from lull.config import get_settings

# the scheme://host[:port] of a network URL: not file:, and not a drive letter for a host
URL_HOST = re.compile(
    r"\b(?!file:)[A-Za-z][A-Za-z0-9+.-]*://(?![A-Za-z]:(?!\d))[^\s/\\?#'\"<>()\x00]+",
    re.IGNORECASE,
)
# file://host, where a drive letter is not a host (file://C:/... keeps its C:)
_FILE_URL = re.compile(r"\bfile://(?![A-Za-z]:)[^\s/\\'\"<>\x00]*", re.IGNORECASE)
# A root ends at a separator or wherever no word goes on: /data is not the start of /datasets
# or /data-2, but [/data] and /data. are cut.
_ROOT_END = r"(?:[\\/]|(?![\w-]))"
# An absolute path (optionally \\?\ or //?/ prefixed) not glued to a word, a ".", a slash or a
# set-aside URL host before it (so not 24/7/365, not a URL's path); it shrinks to its last part.
ABS_PATH = re.compile(
    r"(?<![\w/\\.\x00])(?:\\\\\?\\|//\?/)?(?:[A-Za-z]:[\\/]|/)"
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
    path also /C:/..., MSYS (/c/...), extended-length (\\\\?\\C:\\..., //?/C:/...) and with
    doubled backslashes, as a repr or a JSON string shows it (C:\\\\Users\\\\...)."""
    native = root.rstrip("/\\")
    posix = native.replace("\\", "/")
    forms = {native, posix}
    drive = re.match(r"([A-Za-z]):/(.*)", posix)
    if drive:
        letter, rest = drive.groups()
        back = posix.replace("/", "\\")
        forms |= {back, "/" + posix, f"/{letter}/{rest}", "\\\\?\\" + back, "//?/" + posix}
        forms.add(back.replace("\\", "\\\\"))
    return {f for f in forms if len(f.strip("/\\")) > 1}


class Scrubber:
    """Takes local paths and configured secrets out of text meant for visitors: the run
    folder and other known roots are cut off (input/clip.mp4 stays), any other absolute path
    shrinks to its file name. With `names_only`, a path under a root shrinks to its file name
    too. A URL keeps its scheme and host; no known root survives anywhere in the text."""

    def __init__(self, run_dir: Path, roots: Iterable[Path], *, names_only: bool = False) -> None:
        forms: set[str] = set()
        for root in (run_dir, *roots):
            forms |= root_forms(str(root))
        ordered = sorted(forms, key=len, reverse=True)
        alternatives = "|".join(re.escape(f) for f in ordered)
        self._roots = re.compile(f"(?:{alternatives}){_ROOT_END}", re.IGNORECASE)
        # a stand-in absolute prefix, so ABS_PATH then takes what follows down to its name
        self._cut = "/_/" if names_only else ""
        s = get_settings()
        keys = (s.nebius_api_key, s.tavily_api_key, s.lull_speech_key)
        self._secrets = [k for k in keys if k and len(k) >= 8]

    def __call__(self, text: str) -> str:
        for secret in self._secrets:  # first: a key may sit in a URL's query
            text = text.replace(secret, "***")
        hosts: list[str] = []

        def park(m: re.Match) -> str:
            hosts.append(m.group(0))
            return f"\x00{len(hosts) - 1}\x00"

        def cut(m: re.Match) -> str:  # right after a URL's host, keep the path a path
            return "/" if m.start() and m.string[m.start() - 1] == "\x00" else self._cut

        text = URL_HOST.sub(park, text.replace("\x00", ""))
        text = _FILE_URL.sub("", text)
        text = self._roots.sub(cut, text)
        text = ABS_PATH.sub(lambda m: m.group("name"), text)
        text = re.sub("\x00(\\d+)\x00", lambda m: hosts[int(m.group(1))], text)
        return self._roots.sub("", text)  # the whole text: no root left anywhere

    def tree(self, obj: Any) -> Any:
        """`obj` (JSON data) with every string scrubbed."""
        if isinstance(obj, str):
            return self(obj)
        if isinstance(obj, dict):
            return {k: self.tree(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.tree(v) for v in obj]
        return obj
