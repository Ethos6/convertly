"""Raster images with Pillow. Metadata (EXIF, GPS) is dropped unless asked otherwise."""

from __future__ import annotations

import warnings
from pathlib import Path

from ..registry import Options, Registry

PIL_NAMES = {
    "png": "PNG",
    "jpg": "JPEG",
    "webp": "WEBP",
    "bmp": "BMP",
    "gif": "GIF",
    "tiff": "TIFF",
    "ico": "ICO",
    "tga": "TGA",
    "ppm": "PPM",
    "pcx": "PCX",
    "avif": "AVIF",
}
ANIMATED = {"gif", "webp", "png", "tiff", "avif"}
NO_ALPHA = {"jpg", "bmp", "pcx", "ppm"}
MAX_PIXELS = 250_000_000


def _pillow():
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    return Image


def _can_read(fmt: str) -> bool:
    if fmt == "heic":
        try:
            import pillow_heif  # noqa: F401
        except ImportError:
            return False
        return True
    Image = _pillow()
    Image.init()
    return PIL_NAMES.get(fmt) in Image.OPEN


def _can_write(fmt: str) -> bool:
    Image = _pillow()
    Image.init()
    return PIL_NAMES.get(fmt) in Image.SAVE


def _prepare(img, fmt: str, opts: Options):
    from PIL import ImageOps

    img = ImageOps.exif_transpose(img) or img
    if opts.grayscale:
        img = img.convert("LA" if "A" in img.getbands() else "L")
    if opts.width or opts.height:
        w, h = img.size
        tw = opts.width or round(w * opts.height / h)
        th = opts.height or round(h * opts.width / w)
        scale = min(tw / w, th / h)
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), _pillow().Resampling.LANCZOS)
    if fmt in NO_ALPHA or fmt == "pdf":
        if img.mode in ("RGBA", "LA", "P", "PA"):
            rgba = img.convert("RGBA")
            bg = _pillow().new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.getchannel("A"))
            img = bg if not opts.grayscale else bg.convert("L")
        elif img.mode not in ("RGB", "L", "CMYK") or (fmt != "jpg" and img.mode == "CMYK"):
            img = img.convert("RGB")
    elif img.mode in ("I;16", "I", "F"):
        img = img.convert("RGB")
    return img


def _save_kwargs(fmt: str, opts: Options) -> dict:
    q = opts.quality
    kw: dict = {}
    if fmt == "jpg":
        kw = {"quality": q or 90, "optimize": True, "progressive": True}
    elif fmt == "webp":
        kw = {"quality": q or 85, "method": 4}
    elif fmt == "avif":
        kw = {"quality": q or 70}
    elif fmt == "png":
        kw = {"optimize": True}
    elif fmt == "tiff":
        kw = {"compression": "tiff_lzw"}
    elif fmt == "ico":
        kw = {"sizes": [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]}
    elif fmt == "pdf":
        kw = {"resolution": float(opts.dpi)}
    return kw


def convert_image(src: Path, dst: Path, opts: Options, fmt: str) -> None:
    Image = _pillow()
    if src.suffix.lower() in (".heic", ".heif"):
        import pillow_heif

        pillow_heif.register_heif_opener()
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(src) as im:
            frames = getattr(im, "n_frames", 1)
            kw = _save_kwargs(fmt, opts)
            if frames > 1 and fmt in ANIMATED | {"pdf"}:
                from PIL import ImageSequence

                seq = [_prepare(f.copy(), fmt, opts) for f in ImageSequence.Iterator(im)]
                if fmt in ("gif", "webp", "png", "avif"):
                    kw["duration"] = im.info.get("duration", 100)
                    kw["loop"] = im.info.get("loop", 0)
                if fmt == "png":
                    kw.pop("optimize", None)
                seq[0].save(dst, PIL_NAMES.get(fmt, "PDF"), save_all=True, append_images=seq[1:], **kw)
                return
            img = _prepare(im.copy(), fmt, opts)
            if fmt == "ico":
                side = min(256, max(img.size))
                kw["sizes"] = [s for s in kw["sizes"] if s[0] <= side] or [(side, side)]
            img.save(dst, PIL_NAMES.get(fmt, "PDF"), **kw)


def register(reg: Registry) -> None:
    readable = [f for f in (*PIL_NAMES, "heic") if _can_read(f)]
    writable = [f for f in PIL_NAMES if _can_write(f)]
    for s in readable:
        for d in writable:
            if s == d:
                continue
            reg.add(
                s,
                d,
                (lambda fmt: lambda a, b, o: convert_image(a, b, o, fmt))(d),
                engine="pillow",
                cost=1.0 if d not in ("gif", "ico", "pcx", "ppm", "tga") else 1.5,
            )
        reg.add(s, "pdf", lambda a, b, o: convert_image(a, b, o, "pdf"), engine="pillow", cost=1.2, terminal=True)
