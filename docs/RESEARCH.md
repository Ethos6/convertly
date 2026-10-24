# Research: Filee, and what convertly does differently

[Filee](https://github.com/KnifeLemon/Filee) (MIT, by Myeonggu Kim) is a popular desktop file converter: hold a
key, drag files, and drop them on a "donut" of formats that opens at the cursor. convertly is an independent,
from-scratch project inspired by its ideas. No Filee code, assets or text are used.

Reviewed: the Filee repository as of 24 Oct 2026 (v2.0.0): README, `docs/ARCHITECTURE.md`, `docs/ENGINES.md`,
`SECURITY.md` and the source layout.

## What Filee does well

| Area | Filee |
|---|---|
| Interaction | Global drag gesture + radial menu at the cursor; Explorer/Finder context menu; drop zone; watch folders |
| Breadth | ~256 formats incl. HWP/HWPX, CAD, 3D, geospatial, fonts, e-books, subtitles |
| Routing | `RoutePlanner`: Dijkstra over converter edges, at most 3 hops, engine priority breaks ties; edges can be *ImageSourcesOnly* or *DirectOnly* |
| Output safety | Never overwrites the source; reserves names while a conversion runs; deletes partial output on failure |
| Engines | Many built in (.NET libraries), plus optional FFmpeg, LibreOffice, calibre, Ghostscript, Pandoc downloaded on demand from pinned URLs |
| Polish | 14 UI languages, themes, presets per file type, crop tool, clip editor, auto-update |

## Where there is room for a different tool

1. **Size and platform weight.** ~116k lines of C# across 9 projects, .NET 10, Avalonia, per-OS platform layers
   and an installer. Global input hooks need X11 on Linux (no Wayland gestures) and the macOS build is an
   un-notarized preview. A converter you can `pip install` and script is a different, lighter niche.
2. **Automation.** Filee has a `filee` CLI, but its center of gravity is the GUI. There is no library API for
   other programs, no machine-readable output, and no plugin mechanism for third-party converters.
3. **Server/headless use.** A desktop app with global hooks can't run in CI, containers or on a NAS.
4. **Attack surface.** Converting arbitrary downloads means parsing hostile files (Filee's 2.0.0 notes list
   security fixes for e-books, SVG, 3D models and e-mails). A smaller, auditable core with explicit,
   tested defenses is easier to trust.

## convertly's design

| Decision | Why |
|---|---|
| Python package, CLI + API + local web UI | Runs anywhere, scriptable, no installer; the browser gives a drag-and-drop UI on every OS (including Wayland) without global hooks |
| Same core idea: graph + Dijkstra (max 3 hops) | Proven approach; we add *terminal* edges (multi-file outputs only last) and *direct* edges (only from a genuine source, e.g. GIF → video) so routes stay meaningful (`png → mp3` is refused rather than routed through a GIF) |
| Content sniffing beats file names | A PNG saved as `.jpg` is still read as PNG; a `.docx` is not mistaken for a plain ZIP |
| Atomic, race-free output names (`O_EXCL` placeholders + `os.replace`) | Parallel batches can't overwrite each other; readers never see half-written files |
| Defense in depth for hostile input | Zip-slip / symlink / bomb limits, `defusedxml`, `yaml.safe_load`, Pillow pixel limits, FFmpeg `-protocol_whitelist file`, Pandoc `--sandbox` (+ image filtering for DOCX/ODT/EPUB), LibreOffice with a throw-away profile, CSV/XLSX formula neutralization, every engine call with a timeout and no shell |
| Strip EXIF/GPS | Privacy by default |
| Local web UI hardened like a server | Loopback bind, per-run token in a custom header (CSRF-proof), Host check (DNS-rebinding-proof), strict CSP, size limits, sanitized names |
| Optional engines are found, not downloaded | Nothing fetched at runtime; users install FFmpeg / LibreOffice / Pandoc with their package manager |
| Plugins via entry points | Anyone can add formats without forking |

## Deliberately out of scope for v0.1

Global drag gestures and OS context menus, HWP/HWPX, CAD, 3D, geospatial, fonts, subtitles, e-book formats
beyond EPUB, crop/clip editors, translations. The plugin API is the intended path for these.

## Roadmap ideas

- Video thumbnails (first frame → image) and audio waveform PNGs
- Subtitles (SRT ↔ VTT ↔ ASS) and fonts (TTF ↔ WOFF2) as built-ins
- OCR for scanned PDFs (Tesseract engine)
- PDF password / watermark / page numbers
- Desktop wrapper (tray icon + OS "Open with") around `convertly serve`
- Translations of the web UI
