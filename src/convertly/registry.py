"""Converter registry: every conversion is an edge ``src -> dst`` in a graph."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .formats import normalize

log = logging.getLogger(__name__)


@dataclass
class Options:
    """Options shared by all converters. Converters ignore what does not apply."""

    quality: int | None = None  # 1-100 for lossy images/audio
    width: int | None = None  # resize box (keeps aspect ratio)
    height: int | None = None
    grayscale: bool = False
    dpi: int = 150  # PDF -> image
    pages: str | None = None  # "1-3,5" (PDF)
    sheet: str | None = None  # spreadsheet sheet name
    audio_bitrate: str | None = None  # "192k"
    video_crf: int | None = None
    timeout: float = 600.0  # seconds per external engine call
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.quality is not None and not 1 <= self.quality <= 100:
            raise ValueError("quality must be between 1 and 100")
        for name in ("width", "height"):
            v = getattr(self, name)
            if v is not None and not 1 <= v <= 65535:
                raise ValueError(f"{name} must be between 1 and 65535")
        if not 10 <= self.dpi <= 1200:
            raise ValueError("dpi must be between 10 and 1200")


ConvertFn = Callable[[Path, Path, Options], "list[Path] | None"]


@dataclass(frozen=True)
class Converter:
    src: str
    dst: str
    fn: ConvertFn
    engine: str = "builtin"
    cost: float = 1.0  # higher = less preferred (lossy or slow)
    terminal: bool = False  # may only be the last step (multi-file output)
    direct: bool = False  # may only be the first step (the original file must really be this format)
    available: Callable[[], bool] = lambda: True
    note: str = ""

    def is_available(self) -> bool:
        try:
            return bool(self.available())
        except Exception:  # pragma: no cover - defensive
            log.debug("availability check failed for %s", self, exc_info=True)
            return False


class Registry:
    def __init__(self) -> None:
        self._edges: dict[str, list[Converter]] = {}

    def add(
        self,
        src: str | Iterable[str],
        dst: str | Iterable[str],
        fn: ConvertFn,
        *,
        engine: str = "builtin",
        cost: float = 1.0,
        terminal: bool = False,
        direct: bool = False,
        available: Callable[[], bool] | None = None,
        note: str = "",
        allow_same: bool = False,
    ) -> None:
        srcs = [src] if isinstance(src, str) else list(src)
        dsts = [dst] if isinstance(dst, str) else list(dst)
        for s in srcs:
            for d in dsts:
                s_n, d_n = normalize(s), normalize(d)
                if s_n == d_n and not allow_same:
                    continue
                conv = Converter(s_n, d_n, fn, engine, cost, terminal, direct, available or (lambda: True), note)
                self._edges.setdefault(s_n, []).append(conv)

    def edges_from(self, src: str, *, only_available: bool = True) -> list[Converter]:
        edges = self._edges.get(normalize(src), [])
        return [e for e in edges if not only_available or e.is_available()]

    def all_edges(self, *, only_available: bool = False) -> list[Converter]:
        return [e for edges in self._edges.values() for e in edges if not only_available or e.is_available()]

    def sources(self) -> set[str]:
        return set(self._edges)

    def formats(self) -> set[str]:
        out = set(self._edges)
        for edges in self._edges.values():
            out.update(e.dst for e in edges)
        return out


_default: Registry | None = None


def default_registry() -> Registry:
    """The registry with all built-in converters and installed plugins."""
    global _default
    if _default is None:
        reg = Registry()
        from .converters import register_all

        register_all(reg)
        _load_plugins(reg)
        _default = reg
    return _default


def _load_plugins(reg: Registry) -> None:
    from importlib.metadata import entry_points

    try:
        eps = entry_points(group="convertly.converters")
    except Exception:  # noqa: BLE001  # pragma: no cover
        return
    for ep in eps:
        try:
            ep.load()(reg)
        except Exception:
            log.warning("plugin %s failed to load", ep.name, exc_info=True)
