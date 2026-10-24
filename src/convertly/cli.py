"""Command line: ``convertly convert *.png --to webp``."""

from __future__ import annotations

import argparse
import glob
import json
import logging
import shutil
import sys
from pathlib import Path

from . import __version__, engines
from .formats import FORMATS, category, detect, normalize
from .pipeline import Result, convert_many, plan
from .registry import Options, default_registry
from .router import NoRoute, targets


def _expand(patterns: list[str], recursive: bool, *, unique: bool = True) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        matches = glob.glob(pat, recursive=True) if any(c in pat for c in "*?[") else [pat]
        if not matches:
            print(f"convertly: no files match {pat}", file=sys.stderr)
        for m in matches:
            p = Path(m)
            if p.is_dir():
                it = p.rglob("*") if recursive else p.iterdir()
                out.extend(sorted(x for x in it if x.is_file() and not x.name.startswith(".")))
            else:
                out.append(p)
    if not unique:
        return out
    seen: set[Path] = set()
    unique = []
    for p in out:
        key = p.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _options(a: argparse.Namespace) -> Options:
    return Options(
        quality=a.quality,
        width=a.width,
        height=a.height,
        grayscale=a.grayscale,
        dpi=a.dpi,
        pages=a.pages,
        sheet=a.sheet,
        audio_bitrate=a.audio_bitrate,
        video_crf=a.crf,
        timeout=a.timeout,
    )


def _add_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("conversion options")
    g.add_argument("-q", "--quality", type=int, help="1-100 for JPG/WEBP/AVIF")
    g.add_argument("--width", type=int, help="fit inside this width (images, video)")
    g.add_argument("--height", type=int, help="fit inside this height (images, video)")
    g.add_argument("--grayscale", action="store_true", help="black and white images")
    g.add_argument("--dpi", type=int, default=150, help="PDF page rendering resolution (default 150)")
    g.add_argument("--pages", help='PDF pages, e.g. "1-3,7"')
    g.add_argument("--sheet", help="spreadsheet sheet name")
    g.add_argument("--audio-bitrate", help='e.g. "192k"')
    g.add_argument("--crf", type=int, help="video quality (lower is better, x264 default 23)")
    g.add_argument("--timeout", type=float, default=600.0, help="seconds per engine call (default 600)")


def _print_result(r: Result, as_json: bool) -> None:
    if as_json:
        print(json.dumps(r.to_dict(), ensure_ascii=False), flush=True)
        return
    if r.ok:
        outs = ", ".join(str(p) for p in r.outputs)
        print(f"  ok  {r.source.name} -> {outs}  ({r.seconds:.1f}s, {' > '.join(r.route)})", flush=True)
    else:
        print(f"  ERR {r.source.name}: {r.error}", file=sys.stderr, flush=True)


def cmd_convert(a: argparse.Namespace) -> int:
    files = _expand(a.files, a.recursive)
    if not files:
        print("convertly: nothing to convert", file=sys.stderr)
        return 2
    to = normalize(a.to)
    if a.dry_run:
        rc = 0
        for f in files:
            try:
                sfmt, steps = plan(f, to)
                print(f"{f}: {' > '.join([sfmt, *(f'{s.dst} [{s.engine}]' for s in steps)])}")
            except Exception as exc:  # noqa: BLE001
                print(f"{f}: {exc}", file=sys.stderr)
                rc = 1
        return rc
    try:
        opts = _options(a)
    except ValueError as exc:
        print(f"convertly: {exc}", file=sys.stderr)
        return 2
    results = convert_many(
        files,
        to,
        out_dir=a.output,
        options=opts,
        overwrite=a.overwrite,
        jobs=a.jobs,
        on_result=lambda r: _print_result(r, a.json),
    )
    failed = sum(not r.ok for r in results)
    if not a.json:
        print(f"{len(results) - failed} converted, {failed} failed", file=sys.stderr if failed else sys.stdout)
    return 1 if failed else 0


def cmd_formats(a: argparse.Namespace) -> int:
    if a.what:
        fmt = detect(a.what) if Path(a.what).exists() else normalize(a.what)
        if not fmt:
            print(f"convertly: unknown format: {a.what}", file=sys.stderr)
            return 2
        outs = sorted(targets(fmt))
        if a.json:
            print(json.dumps({"format": fmt, "targets": outs}))
        else:
            print(f"{fmt} -> {' '.join(outs) if outs else '(nothing; an optional engine may be missing)'}")
        return 0
    reg = default_registry()
    by_cat: dict[str, list[str]] = {}
    for fmt in sorted(reg.formats()):
        if any(e.note != "pack" for e in reg.edges_from(fmt)):
            by_cat.setdefault(category(fmt), []).append(fmt)
    if a.json:
        print(json.dumps(by_cat))
    else:
        for cat, fmts in sorted(by_cat.items()):
            print(f"{cat:13} {' '.join(fmts)}")
    return 0


def cmd_route(a: argparse.Namespace) -> int:
    exists = Path(a.source).is_file()
    try:
        sfmt, steps = plan(a.source if exists else f"x.{normalize(a.source)}", a.to)
    except NoRoute as exc:
        print(f"convertly: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"convertly: {exc}", file=sys.stderr)
        return 2
    print(" > ".join([sfmt, *(f"{s.dst} [{s.engine}]" for s in steps)]))
    return 0


def cmd_engines(a: argparse.Namespace) -> int:
    rows = engines.status()
    if a.json:
        print(json.dumps([{"name": e.name, "path": e.path, "version": e.version} for e in rows]))
        return 0
    for e in rows:
        mark = "yes" if e.available else "no "
        detail = e.version or f"not found; enables {engines.INSTALL_HINT[e.name]}"
        print(f"  [{mark}] {e.name:8} {detail}")
    return 0


def cmd_pdf(a: argparse.Namespace) -> int:
    from .converters.pdf import compress_pdf, merge_pdfs, split_pdf
    from .safety import reserve_output

    try:
        opts = Options(pages=getattr(a, "pages", None))
        if a.pdf_cmd == "merge":
            files = _expand(a.files, False, unique=False)
            if len(files) < 2:
                print("convertly: merge needs at least two PDFs", file=sys.stderr)
                return 2
            out = Path(a.output) if a.output else files[0].with_name("merged.pdf")
            dst = reserve_output(out.parent.resolve(), out.name, overwrite=a.overwrite)
            merge_pdfs(files, dst)
            print(dst)
        elif a.pdf_cmd == "split":
            src = Path(a.file)
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                parts = split_pdf(src, Path(tmp) / src.stem, opts)
                for p in parts:
                    dst = reserve_output(src.parent.resolve(), p.name)
                    shutil.move(str(p), dst)
                    print(dst)
        elif a.pdf_cmd == "compress":
            src = Path(a.file)
            dst = reserve_output(src.parent.resolve(), f"{src.stem}-compressed.pdf")
            compress_pdf(src, dst, opts)
            before, after = src.stat().st_size, dst.stat().st_size
            print(f"{dst}  ({before:,} -> {after:,} bytes)")
    except Exception as exc:  # noqa: BLE001
        print(f"convertly: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_watch(a: argparse.Namespace) -> int:
    from .watch import FolderWatcher

    w = FolderWatcher(
        a.folder,
        normalize(a.to),
        out_dir=a.output,
        options=_options(a),
        move_originals=a.move_originals,
        interval=a.interval,
        recursive=a.recursive,
        on_result=lambda r: _print_result(r, a.json),
    )
    print(f"watching {w.folder} -> {w.out_dir} as {a.to} (Ctrl+C to stop)", file=sys.stderr)
    try:
        w.run()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    from .web.server import serve

    return serve(host=a.host, port=a.port, open_browser=not a.no_browser, max_upload_mb=a.max_upload_mb)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="convertly", description="Private, local file conversion.")
    p.add_argument("--version", action="version", version=f"convertly {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="convert files", aliases=["c"])
    c.add_argument("files", nargs="+", help="files, folders or glob patterns")
    c.add_argument("-t", "--to", required=True, help="target format, e.g. webp, pdf, xlsx, mp3")
    c.add_argument("-o", "--output", help="output folder (default: next to each file)")
    c.add_argument("-j", "--jobs", type=int, help="parallel conversions (default: CPU count)")
    c.add_argument("-r", "--recursive", action="store_true", help="include sub-folders of given folders")
    c.add_argument("--overwrite", action="store_true", help="replace existing outputs (never the source)")
    c.add_argument("--dry-run", action="store_true", help="show the route without converting")
    c.add_argument("--json", action="store_true", help="one JSON object per file")
    _add_options(c)
    c.set_defaults(func=cmd_convert)

    f = sub.add_parser("formats", help="list formats, or what a file/format converts to")
    f.add_argument("what", nargs="?")
    f.add_argument("--json", action="store_true")
    f.set_defaults(func=cmd_formats)

    r = sub.add_parser("route", help="show the conversion steps between two formats")
    r.add_argument("source", help="a file or a format")
    r.add_argument("to")
    r.set_defaults(func=cmd_route)

    e = sub.add_parser("engines", help="show optional engines (FFmpeg, LibreOffice, Pandoc)")
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=cmd_engines)

    pdf = sub.add_parser("pdf", help="merge, split or compress PDFs")
    psub = pdf.add_subparsers(dest="pdf_cmd", required=True)
    m = psub.add_parser("merge")
    m.add_argument("files", nargs="+")
    m.add_argument("-o", "--output")
    m.add_argument("--overwrite", action="store_true")
    s = psub.add_parser("split")
    s.add_argument("file")
    s.add_argument("--pages")
    k = psub.add_parser("compress")
    k.add_argument("file")
    k.add_argument("--pages")
    pdf.set_defaults(func=cmd_pdf)

    w = sub.add_parser("watch", help="convert every file that lands in a folder")
    w.add_argument("folder")
    w.add_argument("-t", "--to", required=True)
    w.add_argument("-o", "--output", help="default: <folder>/converted")
    w.add_argument("--move-originals", action="store_true", help="move sources to <folder>/originals")
    w.add_argument("--interval", type=float, default=1.0)
    w.add_argument("-r", "--recursive", action="store_true")
    w.add_argument("--json", action="store_true")
    _add_options(w)
    w.set_defaults(func=cmd_watch)

    sv = sub.add_parser("serve", help="open the drag-and-drop web UI (local only)")
    sv.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--no-browser", action="store_true")
    sv.add_argument("--max-upload-mb", type=int, default=2048)
    sv.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    a = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    return int(a.func(a) or 0)


__all__ = ["FORMATS", "build_parser", "main"]
