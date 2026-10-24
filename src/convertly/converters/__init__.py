"""Built-in converters. Each module exposes ``register(registry)``."""

from __future__ import annotations

from ..registry import Registry


def register_all(reg: Registry) -> None:
    from . import archives, data, documents, images, media, office, pdf, spreadsheets

    for mod in (images, pdf, data, spreadsheets, documents, archives, media, office):
        mod.register(reg)
