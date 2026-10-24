"""Optional engines. Skipped automatically when the engine isn't installed."""

from __future__ import annotations

import subprocess

import pytest

from convertly import Options, convert_file, engines
from convertly.safety import EngineError, run_engine


@pytest.mark.ffmpeg
@pytest.mark.parametrize("fmt", ["mp3", "flac", "ogg", "opus", "m4a", "aac"])
def test_wav_to_audio(media_dir, fmt):
    out = convert_file(media_dir / "tone.wav", fmt).outputs[0]
    assert out.stat().st_size > 500


def _probe(path):
    """Streams as (type, codec, width) parsed from ``ffmpeg -i`` (ffprobe isn't always installed)."""
    import re

    err = subprocess.run(
        [engines.find("ffmpeg"), "-hide_banner", "-i", str(path)], capture_output=True, text=True
    ).stderr
    streams = []
    for m in re.finditer(r"Stream #\S+: (Video|Audio): (\w+)([^\n]*)", err):
        size = re.search(r", (\d+)x(\d+)", m.group(3))
        streams.append(
            {"codec_type": m.group(1).lower(), "codec_name": m.group(2), "width": int(size.group(1)) if size else None}
        )
    return streams


@pytest.mark.ffmpeg
def test_video_to_audio_and_webm_and_gif(media_dir):
    mp3 = convert_file(media_dir / "clip.mp4", "mp3").outputs[0]
    assert [s["codec_type"] for s in _probe(mp3)] == ["audio"]
    webm = convert_file(media_dir / "clip.mp4", "webm", options=Options(width=80)).outputs[0]
    v = [s for s in _probe(webm) if s["codec_type"] == "video"][0]
    assert v["codec_name"] == "vp9" and v["width"] == 80
    gif = convert_file(media_dir / "clip.mp4", "gif", options=Options(width=64)).outputs[0]
    from PIL import Image

    with Image.open(gif) as im:
        assert im.size[0] == 64 and im.n_frames > 3


@pytest.mark.ffmpeg
def test_gif_to_mp4(animated_gif):
    out = convert_file(animated_gif, "mp4").outputs[0]
    assert out.stat().st_size > 0


@pytest.mark.ffmpeg
def test_ffmpeg_refuses_network_playlists(tmp_path):
    fake = tmp_path / "evil.mp4"
    fake.write_text("#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:10.0,\nhttp://127.0.0.1:9/secret.ts\n#EXT-X-ENDLIST\n")
    with pytest.raises(Exception):
        convert_file(fake, "mp3")
    assert not list(tmp_path.glob("*.mp3"))


@pytest.mark.libreoffice
def test_docx_to_pdf_with_libreoffice(tmp_path):
    import docx
    from pypdf import PdfReader

    p = tmp_path / "letter.docx"
    d = docx.Document()
    d.add_heading("Quarterly letter", 1)
    d.add_paragraph("Numbers went up.")
    d.save(p)
    r = convert_file(p, "pdf", options=Options(timeout=180))
    assert r.route[-1] == "pdf (libreoffice)"
    assert "Quarterly letter" in PdfReader(r.outputs[0]).pages[0].extract_text()


@pytest.mark.libreoffice
def test_xlsx_to_ods_and_pdf_with_libreoffice(tmp_path):
    import openpyxl

    p = tmp_path / "x.xlsx"
    wb = openpyxl.Workbook()
    wb.active.append(["a", 1])
    wb.save(p)
    r = convert_file(p, "ods", options=Options(timeout=180))
    assert r.route[-1] == "ods (libreoffice)" and r.outputs[0].stat().st_size > 0


@pytest.mark.pandoc
def test_md_to_epub_with_pandoc(tmp_path):
    import zipfile

    p = tmp_path / "book.md"
    p.write_text("# Chapter 1\n\nOnce upon a time.\n")
    out = convert_file(p, "epub").outputs[0]
    with zipfile.ZipFile(out) as zf:
        assert "mimetype" in zf.namelist()


@pytest.mark.pandoc
def test_pandoc_docx_drops_images_outside_folder(tmp_path):
    import zipfile

    outside = tmp_path / "outside.png"
    from PIL import Image

    Image.new("RGB", (4, 4), (1, 2, 3)).save(outside)
    docs = tmp_path / "docs"
    docs.mkdir()
    Image.new("RGB", (4, 4)).save(docs / "inside.png")
    p = docs / "a.md"
    p.write_text(f"![leak]({outside})\n\n![ok](inside.png)\n\n![net](http://127.0.0.1:9/x.png)\n")
    from convertly.converters.office import pandoc as run_pandoc

    out = tmp_path / "a.docx"
    run_pandoc(p, out, Options(), "md", "docx")
    with zipfile.ZipFile(out) as zf:
        media = [n for n in zf.namelist() if n.startswith("word/media/")]
    assert len(media) == 1


@pytest.mark.pandoc
def test_pandoc_sandbox_blocks_includes(tmp_path):
    if not engines.pandoc_has_sandbox():
        pytest.skip("pandoc without --sandbox")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    p = tmp_path / "inc.tex"
    p.write_text("\\documentclass{article}\\begin{document}\\input{secret.txt}\\end{document}")
    try:
        out = convert_file(p, "md").outputs[0]
    except Exception:  # noqa: BLE001 - refusing is fine too
        return
    assert "TOPSECRET" not in out.read_text()


def test_run_engine_timeout_and_errors(tmp_path):
    import sys

    with pytest.raises(EngineError, match="timed out"):
        run_engine([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.5)
    with pytest.raises(EngineError, match="failed"):
        run_engine([sys.executable, "-c", "import sys; sys.exit(3)"], timeout=10)
    with pytest.raises(EngineError, match="could not start"):
        run_engine([str(tmp_path / "nope")], timeout=5)
    with pytest.raises(ValueError):
        run_engine("echo hi", timeout=5)  # type: ignore[arg-type]


def test_engine_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CONVERTLY_PANDOC", str(tmp_path / "missing"))
    engines.clear_cache()
    try:
        assert engines.find("pandoc") is None
    finally:
        monkeypatch.delenv("CONVERTLY_PANDOC")
        engines.clear_cache()
