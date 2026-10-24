"""convertly: private, local file conversion with automatic multi-step routing.

>>> from convertly import convert
>>> convert("photo.heic", "jpg")          # doctest: +SKIP
"""

from __future__ import annotations

from .formats import detect, normalize
from .pipeline import Result, convert_file, convert_many, plan
from .registry import Options, Registry, default_registry
from .router import NoRoute, common_targets, find_route, targets

__version__ = "0.1.0"


def convert(src, to: str, **kwargs) -> Result:
    """Convert one file. ``kwargs`` go to :func:`convert_file` (``out_dir``, ``options``, ``overwrite``)."""
    return convert_file(src, to, **kwargs)


__all__ = [
    "NoRoute",
    "Options",
    "Registry",
    "Result",
    "__version__",
    "common_targets",
    "convert",
    "convert_file",
    "convert_many",
    "default_registry",
    "detect",
    "find_route",
    "normalize",
    "plan",
    "targets",
]
