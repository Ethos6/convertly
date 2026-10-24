"""Audio and video through FFmpeg (optional engine).

Inputs are opened with ``-protocol_whitelist file`` so a crafted playlist disguised as
a video can't make FFmpeg fetch URLs or read other files (SSRF / local file leaks).
"""

from __future__ import annotations

from pathlib import Path

from .. import engines
from ..registry import Options, Registry
from ..safety import run_engine

AUDIO = ("mp3", "wav", "flac", "ogg", "opus", "m4a", "aac")
VIDEO = ("mp4", "webm", "mkv", "mov", "avi", "mpg")

AUDIO_CODEC = {
    "mp3": ("libmp3lame", "192k"),
    "m4a": ("aac", "192k"),
    "aac": ("aac", "192k"),
    "ogg": ("libvorbis", "192k"),
    "opus": ("libopus", "128k"),
    "flac": ("flac", None),
    "wav": ("pcm_s16le", None),
}
VIDEO_CODEC = {
    "mp4": ("libx264", "aac", ["-pix_fmt", "yuv420p", "-movflags", "+faststart", "-preset", "medium"]),
    "mov": ("libx264", "aac", ["-pix_fmt", "yuv420p", "-preset", "medium"]),
    "mkv": ("libx264", "aac", ["-pix_fmt", "yuv420p", "-preset", "medium"]),
    "webm": ("libvpx-vp9", "libopus", ["-b:v", "0", "-row-mt", "1", "-deadline", "good", "-cpu-used", "4"]),
    "avi": ("mpeg4", "libmp3lame", ["-q:v", "4"]),
    "mpg": ("mpeg2video", "mp2", ["-q:v", "4"]),
}


def _base(src: Path, opts: Options) -> list[str]:
    ff = engines.find("ffmpeg")
    if not ff:
        raise RuntimeError("FFmpeg is not installed")
    return [ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file", "-i", str(src)]


def _scale(opts: Options) -> list[str]:
    if not (opts.width or opts.height):
        return []
    w = opts.width or -2
    h = opts.height or -2
    if opts.width and opts.height:
        return ["-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    return ["-vf", f"scale={w}:{h}"]


def to_audio(src: Path, dst: Path, opts: Options, fmt: str) -> None:
    codec, bitrate = AUDIO_CODEC[fmt]
    args = [*_base(src, opts), "-vn", "-map", "0:a:0", "-c:a", codec]
    if bitrate:
        args += ["-b:a", opts.audio_bitrate or bitrate]
    if fmt == "aac":
        args += ["-f", "adts"]
    args += ["-map_metadata", "0", str(dst)]
    run_engine(args, timeout=opts.timeout)


def to_video(src: Path, dst: Path, opts: Options, fmt: str) -> None:
    vcodec, acodec, extra = VIDEO_CODEC[fmt]
    args = [*_base(src, opts), "-map", "0:v:0", "-map", "0:a:0?", "-c:v", vcodec, *extra]
    crf = opts.video_crf
    if vcodec == "libx264":
        args += ["-crf", str(crf if crf is not None else 23)]
    elif vcodec == "libvpx-vp9":
        args += ["-crf", str(crf if crf is not None else 33)]
    args += [*_scale(opts), "-c:a", acodec, "-b:a", opts.audio_bitrate or "160k", str(dst)]
    run_engine(args, timeout=opts.timeout)


def to_gif(src: Path, dst: Path, opts: Options) -> None:
    width = opts.width or 480
    fps = int(opts.extra.get("fps", 12))
    graph = (
        f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
        "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4"
    )
    args = [*_base(src, opts), "-an", "-filter_complex", graph, "-loop", "0", str(dst)]
    run_engine(args, timeout=opts.timeout)


def register(reg: Registry) -> None:
    def ok(*codecs: str):
        return lambda: engines.find("ffmpeg") is not None and all(c in engines.ffmpeg_encoders() for c in codecs)

    for d in AUDIO:
        codec = AUDIO_CODEC[d][0]
        reg.add(
            [*AUDIO, *VIDEO],
            d,
            (lambda f: lambda a, b, o: to_audio(a, b, o, f))(d),
            engine="ffmpeg",
            available=ok(codec),
            cost=1.0,
        )
    for d in VIDEO:
        vcodec, acodec, _ = VIDEO_CODEC[d]
        reg.add(
            VIDEO,
            d,
            (lambda f: lambda a, b, o: to_video(a, b, o, f))(d),
            engine="ffmpeg",
            available=ok(vcodec, acodec),
            cost=1.0,
        )
        # Animated GIF -> video, only for files that really are GIFs (not "png > gif > mp4").
        reg.add(
            "gif",
            d,
            (lambda f: lambda a, b, o: to_video(a, b, o, f))(d),
            engine="ffmpeg",
            available=ok(vcodec, acodec),
            cost=1.0,
            direct=True,
            terminal=True,
        )
    reg.add(VIDEO, "gif", to_gif, engine="ffmpeg", available=ok("gif"), cost=1.0, terminal=True)
