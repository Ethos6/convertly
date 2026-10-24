"""Run a route: each step writes into a private temp folder; only the finished result
is moved next to the original (or into ``out_dir``) under a name that never clobbers."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .formats import detect, normalize, output_name
from .registry import Converter, Options, Registry, default_registry
from .router import find_route
from .safety import reserve_output, reserve_output_dir

log = logging.getLogger(__name__)


class UnsupportedFile(Exception):
    pass


@dataclass
class Result:
    source: Path
    target: str
    outputs: list[Path] = field(default_factory=list)
    route: list[str] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict:
        return {
            "source": str(self.source),
            "target": self.target,
            "ok": self.ok,
            "outputs": [str(p) for p in self.outputs],
            "route": self.route,
            "seconds": round(self.seconds, 3),
            "error": self.error,
        }


def plan(src: str | Path, to: str, registry: Registry | None = None) -> tuple[str, list[Converter]]:
    path = Path(src)
    sfmt = detect(path)
    if sfmt is None:
        raise UnsupportedFile(f"can't tell what kind of file {path.name} is")
    return sfmt, find_route(sfmt, normalize(to), registry)


def _route_labels(sfmt: str, steps: list[Converter]) -> list[str]:
    return [sfmt, *(f"{s.dst} ({s.engine})" for s in steps)]


def convert_file(
    src: str | Path,
    to: str,
    *,
    out_dir: str | Path | None = None,
    options: Options | None = None,
    overwrite: bool = False,
    registry: Registry | None = None,
) -> Result:
    """Convert one file. Raises on failure; use :func:`convert_many` for a non-raising batch."""
    t0 = time.perf_counter()
    opts = options or Options()
    source = Path(src).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"not a file: {source}")
    target = normalize(to)
    sfmt, steps = plan(source, target, registry or default_registry())
    dest_dir = Path(out_dir).expanduser().resolve() if out_dir else source.parent
    final_name = output_name(source, target)

    with tempfile.TemporaryDirectory(prefix="convertly-") as tmp:
        work = Path(tmp)
        current = source
        produced: list[Path] = []
        for i, step in enumerate(steps):
            step_dir = work / f"step{i}"
            step_dir.mkdir()
            last = i == len(steps) - 1
            name = final_name if last else output_name(source, step.dst)
            dst = step_dir / name
            log.debug("step %d: %s -> %s via %s", i, step.src, step.dst, step.engine)
            ret = step.fn(current, dst, opts)
            produced = [Path(p) for p in ret] if isinstance(ret, (list, tuple)) and ret else [dst]
            missing = [p for p in produced if not p.exists()]
            if missing:
                raise RuntimeError(f"{step.engine} did not produce {missing[0].name}")
            if not last:
                if len(produced) != 1 or produced[0].is_dir():
                    raise RuntimeError(f"intermediate step {step.src}->{step.dst} produced several files")
                current = produced[0]
        outputs = [_finalize(p, dest_dir, overwrite=overwrite, source=source) for p in produced]

    return Result(source, target, outputs, _route_labels(sfmt, steps), time.perf_counter() - t0)


def _finalize(tmp_path: Path, dest_dir: Path, *, overwrite: bool, source: Path) -> Path:
    if tmp_path.is_dir():
        final_dir = reserve_output_dir(dest_dir, tmp_path.name)
        for child in tmp_path.iterdir():
            shutil.move(str(child), final_dir / child.name)
        return final_dir
    if overwrite and (dest_dir / tmp_path.name).resolve() == source:
        overwrite = False  # never replace the original
    final = reserve_output(dest_dir, tmp_path.name, overwrite=overwrite)
    part = final.with_name(f".{final.name}.convertly-part")
    try:
        shutil.move(str(tmp_path), part)
        os.replace(part, final)  # atomic: readers see the old/empty file or the whole result
    except BaseException:
        for p in (part,) if overwrite else (part, final):
            try:
                if p.exists() and (p == part or p.stat().st_size == 0):
                    p.unlink()
            except OSError:
                pass
        raise
    return final


def convert_many(
    files: Iterable[str | Path],
    to: str,
    *,
    out_dir: str | Path | None = None,
    options: Options | None = None,
    overwrite: bool = False,
    jobs: int | None = None,
    registry: Registry | None = None,
    on_result: Callable[[Result], None] | None = None,
) -> list[Result]:
    """Convert many files in parallel. Never raises for a single bad file."""
    reg = registry or default_registry()
    paths = [Path(f) for f in files]
    workers = max(1, min(jobs or (os.cpu_count() or 2), 16, len(paths) or 1))

    def one(p: Path) -> Result:
        t0 = time.perf_counter()
        try:
            r = convert_file(p, to, out_dir=out_dir, options=options, overwrite=overwrite, registry=reg)
        except Exception as exc:
            log.debug("conversion failed", exc_info=True)
            r = Result(p, normalize(to), error=str(exc) or exc.__class__.__name__, seconds=time.perf_counter() - t0)
        if on_result:
            on_result(r)
        return r

    if workers == 1:
        return [one(p) for p in paths]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, paths))
