from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from convertly import engines


def pytest_configure(config):
    for m in ("ffmpeg", "libreoffice", "pandoc"):
        config.addinivalue_line("markers", f"{m}: needs {m}")


def pytest_collection_modifyitems(config, items):
    need = {"ffmpeg": "ffmpeg", "libreoffice": "soffice", "pandoc": "pandoc"}
    for item in items:
        for marker, exe in need.items():
            if marker in item.keywords and not engines.find(exe):
                item.add_marker(pytest.mark.skip(reason=f"{exe} not installed"))


@pytest.fixture
def png(tmp_path: Path) -> Path:
    img = Image.new("RGBA", (64, 40), (255, 0, 0, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 30, 30], fill=(0, 0, 255, 128))
    p = tmp_path / "photo.png"
    img.save(p)
    return p


@pytest.fixture
def jpg_with_gps(tmp_path: Path) -> Path:
    img = Image.new("RGB", (50, 30), (10, 200, 10))
    exif = Image.Exif()
    exif[0x010F] = "SpyCam"  # Make
    exif[0x8825] = {1: "N", 2: (51.0, 30.0, 0.0)}  # GPS IFD
    p = tmp_path / "geo.jpg"
    img.save(p, exif=exif)
    return p


@pytest.fixture
def animated_gif(tmp_path: Path) -> Path:
    frames = [Image.new("RGB", (20, 20), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    p = tmp_path / "anim.gif"
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=80, loop=0)
    return p


@pytest.fixture
def pdf3(tmp_path: Path) -> Path:
    from reportlab.lib.pagesizes import A6
    from reportlab.pdfgen import canvas

    p = tmp_path / "doc.pdf"
    c = canvas.Canvas(str(p), pagesize=A6)
    for i in range(1, 4):
        c.drawString(40, 200, f"Hello page {i}")
        c.showPage()
    c.save()
    return p


@pytest.fixture
def media_dir(tmp_path: Path) -> Path:
    ff = engines.find("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg not installed")
    import subprocess

    subprocess.run(
        [
            ff,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            str(tmp_path / "tone.wav"),
        ],
        check=True,
    )
    subprocess.run(
        [
            ff,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=10:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(tmp_path / "clip.mp4"),
        ],
        check=True,
    )
    return tmp_path


@pytest.fixture
def which():
    return shutil.which
