"""Archives: ZIP and TAR family, repacking and safe extraction (no traversal, no links, bomb limits)."""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

from ..formats import FORMATS
from ..registry import Options, Registry
from ..safety import safe_extract_tar, safe_extract_zip

ARCHIVES = ("zip", "tar", "tar.gz", "tar.bz2", "tar.xz")
_TAR_MODES = {"tar": "w", "tar.gz": "w:gz", "tar.bz2": "w:bz2", "tar.xz": "w:xz"}


def extract(src: Path, fmt: str, dest: Path) -> list[Path]:
    if fmt == "zip":
        return safe_extract_zip(src, dest)
    return safe_extract_tar(src, dest)


def pack(root: Path, files: list[Path], dst: Path, fmt: str) -> None:
    files = sorted(files)
    if fmt == "zip":
        with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for f in files:
                zf.write(f, f.relative_to(root).as_posix())
        return
    with tarfile.open(dst, _TAR_MODES[fmt]) as tf:
        for f in files:
            info = tf.gettarinfo(str(f), arcname=f.relative_to(root).as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with open(f, "rb") as fh:
                tf.addfile(info, fh)


def repack(src: Path, dst: Path, opts: Options, sfmt: str, dfmt: str) -> None:
    with tempfile.TemporaryDirectory(prefix="convertly-") as tmp:
        root = Path(tmp)
        files = extract(src, sfmt, root)
        pack(root, files, dst, dfmt)


def extract_to_dir(src: Path, dst: Path, opts: Options, sfmt: str) -> list[Path]:
    dst.mkdir(parents=True, exist_ok=True)
    extract(src, sfmt, dst)
    return [dst]


def pack_single(src: Path, dst: Path, opts: Options, dfmt: str, arcname: str | None = None) -> None:
    with tempfile.TemporaryDirectory(prefix="convertly-") as tmp:
        root = Path(tmp)
        name = arcname or src.name
        link = root / name
        try:
            os.link(src, link)
        except OSError:
            shutil.copy2(src, link)
        pack(root, [link], dst, dfmt)


def register(reg: Registry) -> None:
    for s in ARCHIVES:
        for d in ARCHIVES:
            if s != d:
                reg.add(s, d, (lambda a_, b_: lambda a, b, o: repack(a, b, o, a_, b_))(s, d), engine="archive")
        reg.add(
            s,
            "dir",
            (lambda a_: lambda a, b, o: extract_to_dir(a, b, o, a_))(s),
            engine="archive",
            terminal=True,
            note="extract",
        )
    # Any single non-archive file can be packed into a ZIP (only as the final step).
    for fmt, info in FORMATS.items():
        if fmt in ARCHIVES or info.category in ("special", "archive"):
            continue
        reg.add(
            fmt,
            "zip",
            lambda a, b, o: pack_single(a, b, o, "zip"),
            engine="archive",
            cost=2.0,
            terminal=True,
            note="pack",
        )
