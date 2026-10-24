"""Watch a folder and convert every file that lands in it (polling, works everywhere)."""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from .formats import detect
from .pipeline import Result, convert_file
from .registry import Options, Registry, default_registry
from .router import NoRoute, find_route
from .safety import reserve_output

log = logging.getLogger(__name__)


class FolderWatcher:
    def __init__(
        self,
        folder: str | Path,
        to: str,
        *,
        out_dir: str | Path | None = None,
        options: Options | None = None,
        move_originals: bool = False,
        interval: float = 1.0,
        recursive: bool = False,
        registry: Registry | None = None,
        on_result: Callable[[Result], None] | None = None,
    ) -> None:
        self.folder = Path(folder).expanduser().resolve()
        if not self.folder.is_dir():
            raise NotADirectoryError(str(self.folder))
        self.to = to
        self.out_dir = Path(out_dir).expanduser().resolve() if out_dir else self.folder / "converted"
        self.originals = self.folder / "originals"
        self.options = options or Options()
        self.move_originals = move_originals
        self.interval = interval
        self.recursive = recursive
        self.registry = registry or default_registry()
        self.on_result = on_result
        self._seen: dict[Path, tuple[int, float]] = {}
        self._done: set[tuple[Path, int, float]] = set()
        self._stop = threading.Event()
        self._ignore = {self.out_dir, self.originals}

    def _candidates(self):
        it = self.folder.rglob("*") if self.recursive else self.folder.iterdir()
        for p in it:
            if not p.is_file() or p.name.startswith(".") or p.name.endswith(".convertly-part"):
                continue
            if any(p.is_relative_to(d) for d in self._ignore):
                continue
            yield p

    def poll_once(self) -> list[Result]:
        """One scan. A file is converted once its size and mtime are unchanged between two scans."""
        results = []
        current: dict[Path, tuple[int, float]] = {}
        for p in self._candidates():
            try:
                st = p.stat()
            except OSError:
                continue
            sig = (st.st_size, st.st_mtime)
            current[p] = sig
            if self._seen.get(p) != sig or (p, *sig) in self._done:
                continue
            self._done.add((p, *sig))
            fmt = detect(p)
            if fmt is None:
                continue
            try:
                find_route(fmt, self.to, self.registry)
            except NoRoute:
                continue
            try:
                r = convert_file(p, self.to, out_dir=self.out_dir, options=self.options, registry=self.registry)
                if self.move_originals:
                    dest = reserve_output(self.originals, p.name)
                    shutil.move(str(p), dest)
            except Exception as exc:  # noqa: BLE001
                r = Result(p, self.to, error=str(exc))
            results.append(r)
            if self.on_result:
                self.on_result(r)
        self._seen = current
        return results

    def run(self) -> None:
        log.info("watching %s -> %s", self.folder, self.out_dir)
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
