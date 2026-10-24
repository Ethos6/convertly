"""Safety helpers: output names that never clobber, safe archive extraction, sandboxed engine calls."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

# Limits for untrusted archives (zip bombs, path traversal).
MAX_ARCHIVE_MEMBERS = 50_000
MAX_ARCHIVE_TOTAL_BYTES = 8 * 1024**3  # 8 GiB uncompressed
MAX_COMPRESSION_RATIO = 250


class UnsafeInput(Exception):
    """The input file looks malicious or exceeds a safety limit."""


class EngineError(Exception):
    """An external engine (ffmpeg, LibreOffice, pandoc) failed."""


def reserve_output(directory: Path, filename: str, *, overwrite: bool = False) -> Path:
    """Atomically claim a free output path in ``directory``.

    ``photo.jpg`` becomes ``photo (1).jpg`` when taken. The returned path exists as an
    empty placeholder created with ``O_EXCL`` so parallel conversions never pick the
    same name. Callers must replace or remove it.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if Path(filename).name != filename or filename in ("", ".", ".."):
        raise UnsafeInput(f"invalid output file name: {filename!r}")
    target = directory / filename
    if overwrite:
        return target
    # keep compound extensions: "a.tar.gz" -> "a (1).tar.gz"
    low = filename.lower()
    comp = next((c for c in ("tar.gz", "tar.bz2", "tar.xz") if low.endswith("." + c)), None)
    if comp:
        stem, dot, ext = filename[: -(len(comp) + 1)], ".", filename[-len(comp) :]
    else:
        stem, dot, ext = filename.rpartition(".")
        if not stem:
            stem, dot, ext = filename, "", ""
    for i in range(0, 10_000):
        name = filename if i == 0 else f"{stem} ({i}){dot}{ext}"
        candidate = directory / name
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        os.close(fd)
        return candidate
    raise OSError(f"no free file name for {filename} in {directory}")


def reserve_output_dir(directory: Path, name: str) -> Path:
    """Like :func:`reserve_output` for a folder."""
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(0, 10_000):
        candidate = directory / (name if i == 0 else f"{name} ({i})")
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise OSError(f"no free folder name for {name} in {directory}")


def _check_member_name(name: str) -> PurePosixPath:
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or (p.parts and ":" in p.parts[0]):
        raise UnsafeInput(f"archive member escapes the target folder: {name!r}")
    return p


def safe_extract_zip(src: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    with zipfile.ZipFile(src) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise UnsafeInput("archive has too many entries")
        total = sum(i.file_size for i in infos)
        packed = max(1, sum(i.compress_size for i in infos))
        if total > MAX_ARCHIVE_TOTAL_BYTES or (total > 50 * 1024**2 and total / packed > MAX_COMPRESSION_RATIO):
            raise UnsafeInput("archive expands to an unsafe size (zip bomb?)")
        for info in infos:
            rel = _check_member_name(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                continue  # never materialise symlinks from untrusted archives
            target = dest.joinpath(*rel.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with zf.open(info) as fin, open(target, "wb") as fout:
                while chunk := fin.read(1 << 20):
                    written += len(chunk)
                    if written > info.file_size + 1024:  # lying header
                        raise UnsafeInput("archive entry larger than declared")
                    fout.write(chunk)
            out.append(target)
    return out


def safe_extract_tar(src: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    with tarfile.open(src, "r:*") as tf:
        members = []
        total = 0
        for i, m in enumerate(tf):
            if i >= MAX_ARCHIVE_MEMBERS:
                raise UnsafeInput("archive has too many entries")
            _check_member_name(m.name)
            if not (m.isfile() or m.isdir()):
                continue  # skip links, devices, fifos
            total += m.size
            if total > MAX_ARCHIVE_TOTAL_BYTES:
                raise UnsafeInput("archive expands to an unsafe size")
            members.append(m)
        for m in members:
            rel = _check_member_name(m.name)
            target = dest.joinpath(*rel.parts)
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            fin = tf.extractfile(m)
            if fin is None:
                continue
            with fin, open(target, "wb") as fout:
                shutil.copyfileobj(fin, fout, 1 << 20)
            out.append(target)
    return out


def run_engine(args: list[str], *, timeout: float, cwd: Path | None = None, env: dict | None = None) -> str:
    """Run an external program without a shell. Returns stdout; raises EngineError."""
    if not args or not isinstance(args, list):
        raise ValueError("args must be a non-empty list")
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise EngineError(f"{Path(args[0]).name} timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise EngineError(f"could not start {args[0]}: {exc}") from exc
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
        raise EngineError(f"{Path(args[0]).name} failed ({proc.returncode}): " + " | ".join(tail))
    return proc.stdout.decode("utf-8", "replace")
