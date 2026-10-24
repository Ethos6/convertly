"""Format names, aliases and detection.

A *format* is a short lowercase id such as ``"png"`` or ``"tar.gz"``. Aliases map
other spellings onto one id (``jpeg`` -> ``jpg``). Detection looks at the file
name first and then sniffs the first bytes, so a PNG saved as ``photo.jpg`` is
still read as PNG.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ALIASES: dict[str, str] = {
    "jpeg": "jpg",
    "jpe": "jpg",
    "jfif": "jpg",
    "tif": "tiff",
    "yml": "yaml",
    "htm": "html",
    "markdown": "md",
    "text": "txt",
    "tgz": "tar.gz",
    "tbz2": "tar.bz2",
    "tbz": "tar.bz2",
    "txz": "tar.xz",
    "ndjson": "jsonl",
    "oga": "ogg",
    "mpeg": "mpg",
}

# Multi-part extensions, longest first.
COMPOUND = ("tar.gz", "tar.bz2", "tar.xz")


@dataclass(frozen=True)
class FormatInfo:
    id: str
    category: str
    label: str


_CATEGORIES: dict[str, tuple[str, ...]] = {
    "image": ("png", "jpg", "webp", "bmp", "gif", "tiff", "ico", "tga", "ppm", "pcx", "avif", "heic"),
    "pdf": ("pdf",),
    "document": ("docx", "doc", "odt", "rtf", "epub", "html", "md", "txt", "rst", "tex"),
    "spreadsheet": ("xlsx", "xls", "ods", "csv", "tsv"),
    "presentation": ("pptx", "ppt", "odp"),
    "data": ("json", "jsonl", "yaml", "toml", "xml"),
    "archive": ("zip", "tar", "tar.gz", "tar.bz2", "tar.xz", "7z"),
    "audio": ("mp3", "wav", "flac", "ogg", "opus", "m4a", "aac"),
    "video": ("mp4", "webm", "mkv", "mov", "avi", "mpg"),
    "special": ("dir", "pages"),
}

FORMATS: dict[str, FormatInfo] = {
    fid: FormatInfo(fid, cat, fid.upper()) for cat, ids in _CATEGORIES.items() for fid in ids
}
# gif is both an image and an animation target for video; keep it an image.


def normalize(name: str) -> str:
    """Return the canonical id for a format name or extension (``.JPEG`` -> ``jpg``)."""
    n = name.strip().lower().lstrip(".")
    return ALIASES.get(n, n)


def category(fmt: str) -> str:
    info = FORMATS.get(normalize(fmt))
    return info.category if info else "other"


def from_name(path: str | Path) -> str | None:
    """Format id from a file name only, or ``None`` if it has no extension."""
    name = Path(path).name.lower()
    for comp in COMPOUND:
        if name.endswith("." + comp) and len(name) > len(comp) + 1:
            return comp
    for alias, target in ALIASES.items():
        if "." in alias and name.endswith("." + alias):
            return target
    suffix = Path(name).suffix
    if not suffix or suffix == name:
        return None
    return normalize(suffix)


_MAGIC: tuple[tuple[bytes, int, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", 0, "png"),
    (b"\xff\xd8\xff", 0, "jpg"),
    (b"GIF87a", 0, "gif"),
    (b"GIF89a", 0, "gif"),
    (b"%PDF-", 0, "pdf"),
    (b"BM", 0, "bmp"),
    (b"II*\x00", 0, "tiff"),
    (b"MM\x00*", 0, "tiff"),
    (b"\x1f\x8b", 0, "gz"),
    (b"BZh", 0, "bz2"),
    (b"\xfd7zXZ\x00", 0, "xz"),
    (b"7z\xbc\xaf\x27\x1c", 0, "7z"),
    (b"fLaC", 0, "flac"),
    (b"OggS", 0, "ogg"),
    (b"ID3", 0, "mp3"),
    (b"\x1aE\xdf\xa3", 0, "mkv"),
    (b"ustar", 257, "tar"),
)

_ZIP_FAMILY = {"zip", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub"}
_SNIFF_CONFLICT_OK = {
    # name-based id -> sniffed ids that agree with it
    "zip": _ZIP_FAMILY,
    "tar.gz": {"gz"},
    "tar.bz2": {"bz2"},
    "tar.xz": {"xz"},
    "webm": {"mkv"},
    "ogg": {"ogg"},
    "opus": {"ogg"},
}


def sniff(path: str | Path) -> str | None:
    """Guess a format from the first bytes of a file."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    for magic, offset, fmt in _MAGIC:
        if head[offset : offset + len(magic)] == magic:
            return fmt
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return "avif"
        if brand in (b"heic", b"heix", b"mif1"):
            return "heic"
        if brand == b"qt  ":
            return "mov"
        if brand.startswith(b"M4A"):
            return "m4a"
        return "mp4"
    if head[:4] == b"PK\x03\x04":
        return "zip"
    return None


def detect(path: str | Path) -> str | None:
    """Detect a file's format: trust the content when it clearly disagrees with the name."""
    by_name = from_name(path)
    sniffed = sniff(path)
    if sniffed is None or by_name is None:
        return by_name or (sniffed if sniffed in FORMATS else None)
    if sniffed == by_name:
        return by_name
    if sniffed in _SNIFF_CONFLICT_OK.get(by_name, ()):  # e.g. a .docx is a ZIP
        return by_name
    if by_name in _ZIP_FAMILY and sniffed == "zip":
        return by_name
    if sniffed in FORMATS and FORMATS[sniffed].category == "image" and category(by_name) == "image":
        return sniffed  # a PNG named .jpg
    if sniffed == "pdf":
        return "pdf"
    return by_name


def output_name(src: Path, fmt: str) -> str:
    """``report.tar.gz`` + ``zip`` -> ``report.zip``."""
    name = src.name
    current = from_name(src)
    if current and name.lower().endswith("." + current):
        stem = name[: -(len(current) + 1)]
    else:
        stem = src.stem if src.suffix else name
    # strip a remaining alias extension like ".jpeg"
    if Path(stem).suffix.lower() in (".tar",):
        stem = Path(stem).stem
    return f"{stem}.{fmt}" if fmt not in ("dir", "pages") else stem
