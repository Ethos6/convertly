"""Local drag-and-drop UI. Standard library only.

Security model:
* binds to 127.0.0.1 by default; files never leave the machine;
* every API call needs a per-run secret token sent in a custom header (blocks CSRF:
  other sites can't add custom headers without a CORS pre-flight we never grant);
* the Host header must be a loopback name (blocks DNS rebinding);
* uploads are size-limited, file names are reduced to a safe base name, and every
  conversion runs in its own temporary folder that is deleted afterwards;
* strict Content-Security-Policy, no third-party assets.
"""

from __future__ import annotations

import hmac
import io
import json
import logging
import os
import re
import secrets
import sys
import tempfile
import threading
import webbrowser
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .. import __version__, engines
from ..formats import category, detect, from_name, normalize
from ..pipeline import convert_file
from ..registry import Options, default_registry
from ..router import common_targets

log = logging.getLogger(__name__)

STATIC = {"/app.js": "application/javascript", "/app.css": "text/css"}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def safe_filename(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name).strip(" .")
    if not name or name in (".", ".."):
        name = "file"
    if len(name) > 180:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:160] + dot + ext[:15]) if dot else name[:180]
    return name


class App:
    def __init__(self, *, max_upload: int, allow_remote: bool = False, token: str | None = None) -> None:
        self.token = token or secrets.token_urlsafe(24)
        self.max_upload = max_upload
        self.allow_remote = allow_remote
        self.slots = threading.BoundedSemaphore(max(1, min(os.cpu_count() or 2, 8)))
        self.registry = default_registry()


def _asset(name: str) -> bytes:
    return resources.files("convertly.web").joinpath(name).read_bytes()


class Handler(BaseHTTPRequestHandler):
    server_version = f"convertly/{__version__}"
    sys_version = ""
    app: App  # set by make_server
    protocol_version = "HTTP/1.1"

    # ---- helpers ----
    def log_message(self, fmt, *args):  # quieter than default
        log.info("%s - %s", self.address_string(), fmt % args)

    def _headers(self, status: int, ctype: str, length: int, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _send(self, status: int, body: bytes, ctype: str = "text/plain; charset=utf-8", extra=None) -> None:
        if status >= 400:  # the request body may be unread: don't reuse the connection
            self.close_connection = True
            extra = {**(extra or {}), "Connection": "close"}
        self._headers(status, ctype, len(body), extra)
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _host_ok(self) -> bool:
        if self.app.allow_remote:
            return True
        host = (self.headers.get("Host") or "").strip().lower()
        hostname = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        return hostname in LOOPBACK_HOSTS

    def _token_ok(self) -> bool:
        sent = self.headers.get("X-Convertly-Token", "")
        return hmac.compare_digest(sent.encode(), self.app.token.encode())

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return origin in (f"http://{host}", f"https://{host}")

    # ---- routes ----
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._host_ok():
            return self._send(HTTPStatus.FORBIDDEN, b"bad host")
        url = urlparse(self.path)
        if url.path == "/":
            q = parse_qs(url.query)
            if not hmac.compare_digest((q.get("t", [""])[0]).encode(), self.app.token.encode()):
                return self._send(
                    HTTPStatus.FORBIDDEN, b"Open the link printed by `convertly serve` (it contains a private token)."
                )
            page = (
                _asset("index.html")
                .decode()
                .replace("{{TOKEN}}", self.app.token)
                .replace("{{VERSION}}", __version__)
                .replace("{{MAX_UPLOAD}}", str(self.app.max_upload))
            )
            return self._send(HTTPStatus.OK, page.encode(), "text/html; charset=utf-8")
        if url.path in STATIC:
            return self._send(HTTPStatus.OK, _asset(url.path.lstrip("/")), STATIC[url.path] + "; charset=utf-8")
        if url.path == "/favicon.ico":
            return self._send(HTTPStatus.NO_CONTENT, b"")
        if url.path == "/api/engines":
            if not self._token_ok():
                return self._json(HTTPStatus.FORBIDDEN, {"error": "bad token"})
            return self._json(HTTPStatus.OK, [{"name": e.name, "available": e.available} for e in engines.status()])
        return self._send(HTTPStatus.NOT_FOUND, b"not found")

    def do_POST(self):
        if not self._host_ok() or not self._origin_ok():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
        if not self._token_ok():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "bad token"})
        url = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._json(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length required"})
        if length < 0:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "bad length"})
        if url.path == "/api/targets":
            if length > 1_000_000:
                return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "too large"})
            return self._targets(self.rfile.read(length))
        if url.path == "/api/convert":
            if length > self.app.max_upload:
                if length <= 256 * 1024**2:
                    self._drain(length)  # lets the client read the 413 instead of a reset
                return self._json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": f"file larger than {self.app.max_upload // 1024**2} MB"},
                )
            return self._convert(parse_qs(url.query), length)
        return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _targets(self, body: bytes):
        try:
            names = json.loads(body or b"{}").get("names", [])
            if not isinstance(names, list) or len(names) > 10_000:
                raise ValueError
        except (ValueError, AttributeError):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": 'expected {"names": [...]}'})
        fmts = sorted({f for f in (from_name(str(n)) for n in names) if f})
        unknown = [str(n) for n in names if not from_name(str(n))]
        outs = common_targets(fmts, self.app.registry) if fmts and not unknown else []
        cats: dict[str, list[str]] = {}
        for t in outs:
            cats.setdefault(category(t), []).append(t)
        return self._json(HTTPStatus.OK, {"sources": fmts, "unknown": unknown, "targets": outs, "byCategory": cats})

    def _convert(self, q: dict, length: int):
        def one(key, cast=str):
            v = q.get(key, [None])[0]
            return cast(v) if v not in (None, "") else None

        try:
            to = normalize(one("to") or "")
            name = safe_filename(one("name") or "file")
            opts = Options(
                quality=one("quality", int),
                width=one("width", int),
                height=one("height", int),
                grayscale=one("grayscale") in ("1", "true", "on"),
                dpi=one("dpi", int) or 150,
                pages=one("pages"),
            )
        except ValueError as exc:
            self._drain(length)
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        if not to:
            self._drain(length)
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "missing target format"})
        with tempfile.TemporaryDirectory(prefix="convertly-web-") as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "in" / name
            src.parent.mkdir()
            remaining = length
            with open(src, "wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    fh.write(chunk)
                    remaining -= len(chunk)
            if remaining:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "upload interrupted"})
            if detect(src) is None:
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": f"unknown file type: {name}"})
            outdir = tmpdir / "out"
            with self.app.slots:
                try:
                    result = convert_file(src, to, out_dir=outdir, options=opts, registry=self.app.registry)
                except Exception as exc:
                    log.debug("web conversion failed", exc_info=True)
                    return self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc) or "conversion failed"})
            files = []
            for p in result.outputs:
                if p.is_dir():
                    files.extend((f, f.relative_to(p.parent)) for f in sorted(p.rglob("*")) if f.is_file())
                else:
                    files.append((p, Path(p.name)))
            if len(files) == 1:
                path, _ = files[0]
                body = path.read_bytes()
                fname = path.name
                ctype = "application/octet-stream"
            else:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for path, arc in files:
                        zf.write(path, arc.as_posix())
                body = buf.getvalue()
                fname = f"{Path(name).stem}-{to}.zip"
                ctype = "application/zip"
            disp = f"attachment; filename=\"{quote(fname)}\"; filename*=UTF-8''{quote(fname)}"
            return self._send(
                HTTPStatus.OK,
                body,
                ctype,
                {
                    "Content-Disposition": disp,
                    "X-Convertly-Filename": quote(fname),
                    "X-Convertly-Route": quote(" > ".join(result.route)),
                },
            )

    def _drain(self, length: int) -> None:
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(1 << 20, remaining))
            if not chunk:
                break
            remaining -= len(chunk)


def make_server(
    host: str = "127.0.0.1", port: int = 8765, *, max_upload_mb: int = 2048, token: str | None = None
) -> tuple[ThreadingHTTPServer, App]:
    allow_remote = host not in ("127.0.0.1", "localhost", "::1")
    app = App(max_upload=max_upload_mb * 1024**2, allow_remote=allow_remote, token=token)
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd, app


def serve(host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = True, max_upload_mb: int = 2048) -> int:
    try:
        httpd, app = make_server(host, port, max_upload_mb=max_upload_mb)
    except OSError as exc:
        print(f"convertly: can't listen on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    real_port = httpd.server_address[1]
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host  # noqa: S104 - display only
    url = f"http://{shown}:{real_port}/?t={app.token}"
    if app.allow_remote:
        print("WARNING: listening beyond this computer. Anyone with the link can convert files here.", file=sys.stderr)
    print(f"convertly {__version__} UI: {url}\nPress Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


__all__ = ["make_server", "safe_filename", "serve"]
