"""PDF: text extraction, rendering to images, splitting, merging, compressing."""

from __future__ import annotations

from pathlib import Path

from ..registry import Options, Registry


def parse_pages(spec: str | None, count: int) -> list[int]:
    """``"1-3,5"`` -> ``[0, 1, 2, 4]`` (0-based). ``None`` means every page."""
    if not spec:
        return list(range(count))
    pages: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            start = int(a) if a else 1
            end = int(b) if b else count
        else:
            start = end = int(part)
        if start < 1 or end < start:
            raise ValueError(f"invalid page range: {part!r}")
        pages.extend(p - 1 for p in range(start, min(end, count) + 1))
    if not pages:
        raise ValueError(f"page range {spec!r} selects no pages (document has {count})")
    return pages


def _reader(src: Path):
    from pypdf import PdfReader

    r = PdfReader(src)
    if r.is_encrypted:
        try:
            ok = r.decrypt("")
        except Exception as exc:  # pragma: no cover - depends on crypto backend
            raise ValueError("PDF is password protected") from exc
        if not ok:
            raise ValueError("PDF is password protected")
    return r


def pdf_to_txt(src: Path, dst: Path, opts: Options) -> None:
    r = _reader(src)
    chunks = []
    for i in parse_pages(opts.pages, len(r.pages)):
        chunks.append((r.pages[i].extract_text() or "").rstrip())
    dst.write_text("\n\n\f".join(chunks).strip() + "\n", encoding="utf-8")


def pdf_to_images(src: Path, dst: Path, opts: Options, fmt: str) -> list[Path]:
    import pypdfium2 as pdfium

    from .images import convert_image

    doc = pdfium.PdfDocument(src)
    try:
        pages = parse_pages(opts.pages, len(doc))
        out: list[Path] = []
        for i in pages:
            bitmap = doc[i].render(scale=opts.dpi / 72)
            img = bitmap.to_pil()
            target = dst if len(pages) == 1 else dst.with_name(f"{dst.stem}-p{i + 1}{dst.suffix}")
            tmp_png = target.with_suffix(".render.png")
            img.save(tmp_png, "PNG")
            convert_image(tmp_png, target, opts, fmt)
            tmp_png.unlink()
            out.append(target)
        return out
    finally:
        doc.close()


def split_pdf(src: Path, dst: Path, opts: Options) -> list[Path]:
    from pypdf import PdfWriter

    r = _reader(src)
    out = []
    for i in parse_pages(opts.pages, len(r.pages)):
        w = PdfWriter()
        w.add_page(r.pages[i])
        target = dst.with_name(f"{dst.name}-p{i + 1}.pdf")
        with open(target, "wb") as fh:
            w.write(fh)
        out.append(target)
    return out


def merge_pdfs(sources: list[Path], dst: Path) -> None:
    from pypdf import PdfWriter

    w = PdfWriter()
    for s in sources:
        r = _reader(s)
        for p in r.pages:
            w.add_page(p)
    with open(dst, "wb") as fh:
        w.write(fh)


def compress_pdf(src: Path, dst: Path, opts: Options) -> None:
    from pypdf import PdfWriter

    r = _reader(src)
    w = PdfWriter()
    for i in parse_pages(opts.pages, len(r.pages)):
        w.add_page(r.pages[i])
    for page in w.pages:
        page.compress_content_streams()
    import inspect

    params = inspect.signature(w.compress_identical_objects).parameters
    if "remove_duplicates" in params:  # pypdf >= 6
        w.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    else:
        w.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    with open(dst, "wb") as fh:
        w.write(fh)


def register(reg: Registry) -> None:
    reg.add("pdf", "txt", pdf_to_txt, engine="pypdf", cost=1.0)
    reg.add("pdf", "pages", split_pdf, engine="pypdf", terminal=True)
    try:
        import pypdfium2  # noqa: F401
    except ImportError:  # pragma: no cover
        return
    for fmt in ("png", "jpg", "webp", "tiff"):
        reg.add("pdf", fmt, (lambda f: lambda a, b, o: pdf_to_images(a, b, o, f))(fmt), engine="pdfium", terminal=True)
