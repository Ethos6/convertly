"""CLI, folder watching and the local web UI (including its security checks)."""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from urllib.parse import quote

import pytest

from convertly.cli import main
from convertly.watch import FolderWatcher
from convertly.web.server import make_server, safe_filename

# ---------- CLI ----------


def test_cli_convert_glob_and_json(png, tmp_path, capsys):
    (tmp_path / "second.png").write_bytes(png.read_bytes())
    rc = main(["convert", str(tmp_path / "*.png"), "--to", "webp", "--json", "-o", str(tmp_path / "out")])
    assert rc == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert len(lines) == 2 and all(x["ok"] for x in lines)
    assert sorted(Path(x["outputs"][0]).name for x in lines) == ["photo.webp", "second.webp"]


def test_cli_folder_recursive(png, tmp_path, capsys):
    sub = tmp_path / "in" / "deep"
    sub.mkdir(parents=True)
    (sub / "a.png").write_bytes(png.read_bytes())
    assert main(["convert", str(tmp_path / "in"), "--to", "jpg", "-r"]) == 0
    assert (sub / "a.jpg").exists()


def test_cli_failure_exit_code(tmp_path, capsys):
    bad = tmp_path / "x.png"
    bad.write_bytes(b"nope")
    assert main(["convert", str(bad), "--to", "jpg"]) == 1
    assert "ERR" in capsys.readouterr().err


def test_cli_no_files(tmp_path, capsys):
    assert main(["convert", str(tmp_path / "*.nothing"), "--to", "jpg"]) == 2


def test_cli_bad_option(png, capsys):
    assert main(["convert", str(png), "--to", "jpg", "--quality", "500"]) == 2


def test_cli_dry_run(png, capsys):
    assert main(["convert", str(png), "--to", "webp", "--dry-run"]) == 0
    assert "png > webp [pillow]" in capsys.readouterr().out
    assert not png.with_suffix(".webp").exists()


def test_cli_formats_route_engines(png, capsys):
    assert main(["formats"]) == 0
    assert "image" in capsys.readouterr().out
    assert main(["formats", str(png), "--json"]) == 0
    assert "webp" in json.loads(capsys.readouterr().out)["targets"]
    assert main(["route", "md", "pdf"]) == 0
    assert capsys.readouterr().out.startswith("md >")
    assert main(["route", "png", "mp3"]) == 1
    assert main(["engines", "--json"]) == 0
    assert {e["name"] for e in json.loads(capsys.readouterr().out)} == {"ffmpeg", "soffice", "pandoc"}


def test_cli_pdf_tools(pdf3, tmp_path, capsys):
    from pypdf import PdfReader

    out = tmp_path / "joined.pdf"
    assert main(["pdf", "merge", str(pdf3), str(pdf3), "-o", str(out)]) == 0
    assert len(PdfReader(out).pages) == 6
    assert main(["pdf", "split", str(pdf3), "--pages", "1,3"]) == 0
    assert sorted(p.name for p in tmp_path.glob("doc-p*.pdf")) == ["doc-p1.pdf", "doc-p3.pdf"]
    assert main(["pdf", "compress", str(pdf3)]) == 0
    assert (tmp_path / "doc-compressed.pdf").exists()
    assert main(["pdf", "merge", str(pdf3)]) == 2


def test_cli_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert "convertly" in capsys.readouterr().out


def test_module_entry_point():
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-m", "convertly", "--version"], capture_output=True, text=True)
    assert out.returncode == 0 and "convertly" in out.stdout


# ---------- watch ----------


def test_watch_converts_stable_files_once(png, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    w = FolderWatcher(inbox, "jpg", move_originals=True)
    (inbox / "a.png").write_bytes(png.read_bytes())
    (inbox / "notes.unknownext").write_text("x")
    assert w.poll_once() == []  # first sighting: wait until stable
    results = w.poll_once()
    assert len(results) == 1 and results[0].ok
    assert (inbox / "converted" / "a.jpg").exists()
    assert (inbox / "originals" / "a.png").exists() and not (inbox / "a.png").exists()
    assert w.poll_once() == []  # outputs and originals are ignored


def test_watch_requires_folder(tmp_path):
    with pytest.raises(NotADirectoryError):
        FolderWatcher(tmp_path / "missing", "jpg")


def test_watch_thread_stops(tmp_path):
    w = FolderWatcher(tmp_path, "jpg", interval=0.05)
    t = threading.Thread(target=w.run)
    t.start()
    w.stop()
    t.join(timeout=2)
    assert not t.is_alive()


# ---------- web ----------


@pytest.fixture
def server():
    httpd, app = make_server("127.0.0.1", 0, max_upload_mb=1, token="testtoken")
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd.server_address[1], app
    httpd.shutdown()
    httpd.server_close()


def req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    h = {"Host": f"127.0.0.1:{port}"}
    h.update(headers or {})
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    data = r.read()
    hdrs = dict(r.getheaders())
    conn.close()
    return r.status, hdrs, data


AUTH = {"X-Convertly-Token": "testtoken"}


def test_page_needs_token_and_sets_csp(server):
    port, _ = server
    assert req(port, "GET", "/")[0] == 403
    status, hdrs, body = req(port, "GET", "/?t=testtoken")
    assert status == 200 and b"convertly" in body and b"{{" not in body
    assert b'content="1048576"' in body
    assert "default-src 'none'" in hdrs["Content-Security-Policy"]
    assert hdrs["X-Content-Type-Options"] == "nosniff"
    for asset in ("/app.js", "/app.css"):
        assert req(port, "GET", asset)[0] == 200


def test_dns_rebinding_host_rejected(server):
    port, _ = server
    status, _, _ = req(port, "GET", "/?t=testtoken", headers={"Host": f"evil.example:{port}"})
    assert status == 403
    status, _, _ = req(port, "POST", "/api/targets", b"{}", headers={**AUTH, "Host": "attacker.test"})
    assert status == 403


def test_api_requires_token_and_same_origin(server, png):
    port, _ = server
    body = png.read_bytes()
    assert req(port, "POST", "/api/convert?to=jpg&name=a.png", body)[0] == 403
    assert req(port, "POST", "/api/convert?to=jpg&name=a.png", body, {"X-Convertly-Token": "wrong"})[0] == 403
    status, _, _ = req(port, "POST", "/api/convert?to=jpg&name=a.png", body, {**AUTH, "Origin": "https://evil.example"})
    assert status == 403
    assert req(port, "GET", "/api/engines")[0] == 403


def test_targets_endpoint(server):
    port, _ = server
    status, _, data = req(port, "POST", "/api/targets", json.dumps({"names": ["a.png", "b.jpg"]}).encode(), AUTH)
    info = json.loads(data)
    assert status == 200 and "webp" in info["targets"] and "pdf" in info["targets"]
    status, _, data = req(port, "POST", "/api/targets", json.dumps({"names": ["weird"]}).encode(), AUTH)
    assert json.loads(data)["unknown"] == ["weird"]
    assert req(port, "POST", "/api/targets", b"not json", AUTH)[0] == 400


def test_convert_endpoint(server, png):
    port, _ = server
    status, hdrs, data = req(port, "POST", "/api/convert?to=jpg&name=" + quote("my photo.png"), png.read_bytes(), AUTH)
    assert status == 200 and data[:3] == b"\xff\xd8\xff"
    assert "my%20photo.jpg" in hdrs["X-Convertly-Filename"]
    assert "attachment" in hdrs["Content-Disposition"]


def test_convert_multi_output_is_zipped(server, pdf3):
    import io
    import zipfile

    port, _ = server
    status, hdrs, data = req(port, "POST", "/api/convert?to=png&dpi=40&name=doc.pdf", pdf3.read_bytes(), AUTH)
    assert status == 200 and hdrs["Content-Type"] == "application/zip"
    assert sorted(zipfile.ZipFile(io.BytesIO(data)).namelist()) == ["doc-p1.png", "doc-p2.png", "doc-p3.png"]


def test_convert_errors(server, png):
    port, _ = server
    assert req(port, "POST", "/api/convert?to=mp3&name=a.png", png.read_bytes(), AUTH)[0] == 422
    assert req(port, "POST", "/api/convert?name=a.png", png.read_bytes(), AUTH)[0] == 400
    assert req(port, "POST", "/api/convert?to=jpg&quality=abc&name=a.png", png.read_bytes(), AUTH)[0] == 400
    assert req(port, "POST", "/api/convert?to=jpg&quality=900&name=a.png", png.read_bytes(), AUTH)[0] == 400
    assert req(port, "POST", "/api/convert?to=jpg&name=noext", b"\x00\x01", AUTH)[0] == 415
    big = b"\0" * (2 * 1024 * 1024)
    assert req(port, "POST", "/api/convert?to=jpg&name=a.png", big, AUTH)[0] == 413


def test_path_traversal_name_is_contained(server, png, tmp_path):
    port, _ = server
    status, hdrs, _ = req(
        port, "POST", "/api/convert?to=jpg&name=" + quote("../../../../tmp/pwn.png"), png.read_bytes(), AUTH
    )
    assert status == 200 and hdrs["X-Convertly-Filename"] == "pwn.jpg"


@pytest.mark.parametrize(
    ("raw", "want"),
    [("../../etc/passwd", "passwd"), ("C:\\x\\y.png", "y.png"), ("..", "file"), ("a<b>.png", "a_b_.png"), ("", "file")],
)
def test_safe_filename(raw, want):
    assert safe_filename(raw) == want
