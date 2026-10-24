# Security policy

## Reporting a vulnerability

Please report privately through **GitHub → Security → Report a vulnerability**
(https://github.com/Ethos6/convertly/security/advisories/new), or email marketing@atomesus.com.
Don't open a public issue. We aim to acknowledge within 3 working days and to ship a fix for
confirmed issues within 30 days, crediting you unless you'd rather not be named.

## Supported versions

The latest release receives security fixes.

## Threat model

convertly is built to open files you didn't create (downloads, attachments). Defenses, each covered by tests:

| Threat | Defense |
|---|---|
| Archive path traversal (zip-slip), absolute paths, drive letters | every member path is checked before anything is written |
| Symlinks / hard links / devices in archives | skipped, never created |
| Zip / decompression bombs | entry count, total size and compression-ratio limits; Pillow pixel limit (bomb warnings are errors) |
| XML entity expansion (billion laughs) and XXE | `defusedxml` |
| YAML code execution | `yaml.safe_load` only |
| FFmpeg fetching URLs or local files via crafted playlists | `-protocol_whitelist file` on every input |
| Pandoc including local files or URLs | `--sandbox`; for DOCX/ODT/EPUB output, images that point outside the document's folder or at URLs are removed |
| Spreadsheet formula injection in CSV / XLSX output | formula-like text is prefixed with `'` in CSV and stored as text in XLSX |
| Overwriting user files / races between parallel jobs | `O_EXCL` name reservation, atomic `os.replace`, the source is never a target |
| Hung engines | every external call has a timeout and runs without a shell |
| Location leaks | EXIF / GPS dropped from converted images |

Web UI (`convertly serve`): binds to 127.0.0.1 by default; per-run random token required in the
`X-Convertly-Token` header (CSRF); Host header must be a loopback name (DNS rebinding); Origin checked;
strict Content-Security-Policy with no third-party sources; upload size limit; uploaded names reduced to a
safe base name; each request converts inside its own temporary folder, deleted afterwards.

## Out of scope

Bugs inside optional engines (FFmpeg, LibreOffice, Pandoc): report them upstream and keep those tools updated.
Running `convertly serve --host 0.0.0.0` exposes the converter to your network by design.
