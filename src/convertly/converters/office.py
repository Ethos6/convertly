"""Office formats through LibreOffice and Pandoc (both optional).

LibreOffice runs headless with a throw-away profile per call, so parallel conversions
don't fight over the user's profile lock. Pandoc runs with ``--sandbox`` when available,
so documents can't pull in local files or URLs.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from .. import engines
from ..registry import Options, Registry
from ..safety import EngineError, run_engine

WRITER = ("doc", "docx", "odt", "rtf")
CALC = ("xls", "xlsx", "ods")
IMPRESS = ("ppt", "pptx", "odp")

LO_FILTERS = {"txt": "txt:Text (encoded):UTF8", "csv": "csv:Text - txt - csv (StarCalc):44,34,76,1"}


def libreoffice(src: Path, dst: Path, opts: Options, fmt: str) -> None:
    soffice = engines.find("soffice")
    if not soffice:
        raise RuntimeError("LibreOffice is not installed")
    with tempfile.TemporaryDirectory(prefix="convertly-lo-") as tmp:
        tmpdir = Path(tmp)
        profile = (tmpdir / "profile").resolve().as_uri()
        outdir = tmpdir / "out"
        outdir.mkdir()
        run_engine(
            [
                soffice,
                f"-env:UserInstallation={profile}",
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--convert-to",
                LO_FILTERS.get(fmt, fmt),
                "--outdir",
                str(outdir),
                str(src),
            ],
            timeout=opts.timeout,
        )
        produced = [p for p in outdir.iterdir() if p.is_file()]
        if not produced:
            raise EngineError("LibreOffice produced no output (unsupported or damaged file?)")
        shutil.move(str(produced[0]), dst)


PANDOC_IN = {
    "md": "markdown",
    "html": "html",
    "docx": "docx",
    "odt": "odt",
    "epub": "epub",
    "rst": "rst",
    "tex": "latex",
}
PANDOC_OUT = {
    "md": "gfm",
    "html": "html5",
    "docx": "docx",
    "odt": "odt",
    "epub": "epub3",
    "rst": "rst",
    "tex": "latex",
    "txt": "plain",
}


BINARY_OUT = ("docx", "odt", "epub")


def _safe_image(src: str, base: Path) -> bool:
    if not src or "://" in src or src.startswith(("data:", "/", "\\")) or ":" in src.split("/")[0]:
        return False
    try:
        return (base / src).resolve().is_relative_to(base.resolve())
    except (OSError, ValueError):
        return False


def _strip_unsafe_images(node, base: Path):
    """Replace images that point outside the document's folder (or at URLs) with their alt text."""
    if isinstance(node, list):
        return [_strip_unsafe_images(n, base) for n in node]
    if isinstance(node, dict):
        if node.get("t") == "Image":
            attr, alt, target = node["c"]
            if not _safe_image(target[0], base):
                return {"t": "Span", "c": [attr, _strip_unsafe_images(alt, base)]}
        return {k: _strip_unsafe_images(v, base) for k, v in node.items()}
    return node


def pandoc(src: Path, dst: Path, opts: Options, sfmt: str, dfmt: str) -> None:
    exe = engines.find("pandoc")
    if not exe:
        raise RuntimeError("Pandoc is not installed")
    sandbox = ["--sandbox"] if engines.pandoc_has_sandbox() else []
    meta = ["--standalone", "--metadata", f"title={src.stem}"] if dfmt in ("html", "epub", "tex") else []
    if dfmt not in BINARY_OUT:
        run_engine(
            [exe, *sandbox, "-f", PANDOC_IN[sfmt], "-t", PANDOC_OUT[dfmt], *meta, "-o", str(dst), str(src)],
            timeout=opts.timeout,
            cwd=src.parent,
        )
        return
    # Binary writers need pandoc's data files, which some builds can't read under --sandbox.
    # So: read sandboxed into the JSON AST, drop images that point outside the folder, then write.
    ast_json = run_engine(
        [exe, *sandbox, "-f", PANDOC_IN[sfmt], "-t", "json", str(src)], timeout=opts.timeout, cwd=src.parent
    )
    ast = _strip_unsafe_images(json.loads(ast_json), src.parent)
    with tempfile.TemporaryDirectory(prefix="convertly-pd-") as tmp:
        ast_file = Path(tmp) / "doc.json"
        ast_file.write_text(json.dumps(ast), encoding="utf-8")
        run_engine(
            [
                exe,
                "-f",
                "json",
                "-t",
                PANDOC_OUT[dfmt],
                *meta,
                f"--resource-path={src.parent}",
                "-o",
                str(dst),
                str(ast_file),
            ],
            timeout=opts.timeout,
            cwd=src.parent,
        )


def register(reg: Registry) -> None:
    has_lo = lambda: engines.find("soffice") is not None  # noqa: E731
    has_pd = lambda: engines.find("pandoc") is not None  # noqa: E731

    def lo(fmt):
        return lambda a, b, o: libreoffice(a, b, o, fmt)

    for group, outs in (
        (WRITER, (*WRITER, "pdf", "html", "txt")),
        (CALC, (*CALC, "pdf", "csv")),
        (IMPRESS, (*IMPRESS, "pdf")),
    ):
        for s in group:
            for d in outs:
                if s == d or d == "doc" or d == "xls" or d == "ppt":
                    continue  # don't produce legacy binary formats
                reg.add(s, d, lo(d), engine="libreoffice", available=has_lo, cost=1.0 if d == "pdf" else 1.4)
    for s in PANDOC_IN:
        for d in PANDOC_OUT:
            if s != d:
                reg.add(
                    s,
                    d,
                    (lambda a_, b_: lambda a, b, o: pandoc(a, b, o, a_, b_))(s, d),
                    engine="pandoc",
                    available=has_pd,
                    cost=1.1,
                )
