"""Optional external engines: FFmpeg, LibreOffice, Pandoc. Found on PATH or via env vars."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from dataclasses import dataclass

ENV = {
    "ffmpeg": "CONVERTLY_FFMPEG",
    "soffice": "CONVERTLY_SOFFICE",
    "pandoc": "CONVERTLY_PANDOC",
}
CANDIDATES = {
    "ffmpeg": ("ffmpeg",),
    "soffice": (
        "soffice",
        "libreoffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
    ),
    "pandoc": ("pandoc",),
}
INSTALL_HINT = {
    "ffmpeg": "audio and video (https://ffmpeg.org/download.html)",
    "soffice": "DOC/DOCX/ODT/RTF/XLS/PPT to PDF and between Office formats (https://www.libreoffice.org)",
    "pandoc": "Markdown/HTML/DOCX/EPUB/LaTeX/RST/ODT documents (https://pandoc.org/installing.html)",
}


@dataclass(frozen=True)
class Engine:
    name: str
    path: str | None
    version: str | None

    @property
    def available(self) -> bool:
        return self.path is not None


@functools.cache
def find(name: str) -> str | None:
    override = os.environ.get(ENV[name])
    if override:
        return override if os.path.isfile(override) and os.access(override, os.X_OK) else None
    for cand in CANDIDATES[name]:
        found = shutil.which(cand)
        if found:
            return found
    return None


@functools.cache
def version(name: str) -> str | None:
    path = find(name)
    if not path:
        return None
    flag = "-version" if name == "ffmpeg" else "--version"
    try:
        out = subprocess.run(
            [path, flag], capture_output=True, timeout=30, check=False, stdin=subprocess.DEVNULL
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    first = out.strip().splitlines()[0] if out.strip() else ""
    return first[:120] or None


def status() -> list[Engine]:
    return [Engine(n, find(n), version(n)) for n in ENV]


@functools.cache
def ffmpeg_encoders() -> frozenset[str]:
    path = find("ffmpeg")
    if not path:
        return frozenset()
    try:
        out = subprocess.run(
            [path, "-hide_banner", "-encoders"], capture_output=True, timeout=30, check=False, stdin=subprocess.DEVNULL
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)


@functools.cache
def pandoc_has_sandbox() -> bool:
    v = version("pandoc") or ""
    try:
        nums = [int(x) for x in v.split()[1].split(".")[:2]]
    except (IndexError, ValueError):
        return False
    return nums >= [2, 15]


def clear_cache() -> None:
    for fn in (find, version, ffmpeg_encoders, pandoc_has_sandbox):
        fn.cache_clear()
