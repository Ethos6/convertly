"""Formats, routing, registry and the pipeline's file-safety guarantees."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from convertly import NoRoute, Options, Registry, convert_file, convert_many, detect, find_route, normalize, targets
from convertly.formats import from_name, output_name
from convertly.safety import reserve_output

# ---------- formats ----------


@pytest.mark.parametrize(
    ("raw", "want"),
    [("JPEG", "jpg"), (".tif", "tiff"), ("yml", "yaml"), ("tgz", "tar.gz"), ("Markdown", "md"), ("png", "png")],
)
def test_normalize(raw, want):
    assert normalize(raw) == want


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("a.TAR.GZ", "tar.gz"),
        ("photo.jpeg", "jpg"),
        ("x.tgz", "tar.gz"),
        ("README", None),
        (".bashrc", None),
        ("a.b.png", "png"),
    ],
)
def test_from_name(name, want):
    assert from_name(name) == want


def test_detect_trusts_content_for_misnamed_image(png, tmp_path):
    wrong = tmp_path / "really_png.jpg"
    wrong.write_bytes(png.read_bytes())
    assert detect(wrong) == "png"


def test_detect_docx_is_not_zip(tmp_path):
    import docx

    p = tmp_path / "a.docx"
    docx.Document().save(p)
    assert detect(p) == "docx"


def test_detect_no_extension_uses_magic(png, tmp_path):
    p = tmp_path / "noext"
    p.write_bytes(png.read_bytes())
    assert detect(p) == "png"


@pytest.mark.parametrize(
    ("src", "fmt", "want"),
    [
        ("a.tar.gz", "zip", "a.zip"),
        ("photo.jpeg", "png", "photo.png"),
        ("my.report.docx", "pdf", "my.report.pdf"),
        ("arc.zip", "dir", "arc"),
    ],
)
def test_output_name(src, fmt, want):
    assert output_name(Path(src), fmt) == want


# ---------- router ----------


def _reg():
    reg = Registry()
    noop = lambda a, b, o: b.write_bytes(a.read_bytes())  # noqa: E731
    reg.add("a", "b", noop, cost=1)
    reg.add("b", "c", noop, cost=1)
    reg.add("a", "c", noop, cost=5)
    reg.add("c", "d", noop, cost=1)
    reg.add("a", "multi", noop, terminal=True)
    reg.add("multi", "z", noop)
    reg.add("x", "y", noop, available=lambda: False)
    return reg


def test_route_prefers_cheapest_chain():
    steps = find_route("a", "c", _reg())
    assert [s.dst for s in steps] == ["b", "c"]


def test_route_multi_hop():
    assert [s.dst for s in find_route("a", "d", _reg())] == ["b", "c", "d"]


def test_terminal_edge_only_last():
    reg = _reg()
    assert [s.dst for s in find_route("a", "multi", reg)] == ["multi"]
    with pytest.raises(NoRoute):
        find_route("a", "z", reg)


def test_unavailable_edges_ignored():
    with pytest.raises(NoRoute):
        find_route("x", "y", _reg())


def test_max_hops():
    with pytest.raises(NoRoute):
        find_route("a", "d", _reg(), max_hops=1)


def test_targets():
    t = targets("a", _reg())
    assert {"b", "c", "d", "multi"} <= t and "z" not in t and "a" not in t


def test_real_registry_routes():
    assert find_route("png", "webp")[0].engine == "pillow"
    # docx -> pdf always has a route: LibreOffice if present, else via Markdown.
    assert find_route("docx", "pdf")[-1].dst == "pdf"
    assert "xlsx" in targets("csv")


# ---------- options ----------


@pytest.mark.parametrize("kw", [{"quality": 0}, {"quality": 101}, {"width": 0}, {"dpi": 5}])
def test_options_validate(kw):
    with pytest.raises(ValueError):
        Options(**kw)


# ---------- pipeline safety ----------


def test_output_next_to_source_and_unique(png):
    r1 = convert_file(png, "jpg")
    r2 = convert_file(png, "jpg")
    assert r1.outputs[0].name == "photo.jpg"
    assert r2.outputs[0].name == "photo (1).jpg"
    assert png.exists()


def test_never_overwrites_source_even_with_overwrite(tmp_path, png):
    jpg = convert_file(png, "jpg").outputs[0]
    before = jpg.read_bytes()
    r = convert_file(jpg, "jpg", overwrite=True, registry=_same_format_registry())
    assert r.outputs[0] != jpg
    assert jpg.read_bytes() == before


def _same_format_registry():
    reg = Registry()
    reg.add("jpg", "jpg", lambda a, b, o: None if b.write_bytes(a.read_bytes() + b"x") else None, allow_same=True)
    return reg


def test_overwrite_replaces_previous_output(png):
    first = convert_file(png, "bmp").outputs[0]
    first.write_bytes(b"old")
    r = convert_file(png, "bmp", overwrite=True)
    assert r.outputs[0] == first and first.read_bytes() != b"old"


def test_out_dir(png, tmp_path):
    out = tmp_path / "out" / "nested"
    r = convert_file(png, "webp", out_dir=out)
    assert r.outputs[0].parent == out.resolve()


def test_failure_leaves_nothing_behind(tmp_path):
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\nnot really")
    with pytest.raises(Exception):
        convert_file(bad, "jpg")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["broken.png"]


def test_unknown_file_type(tmp_path):
    p = tmp_path / "mystery"
    p.write_bytes(b"\x00\x01\x02")
    with pytest.raises(Exception, match="can't tell"):
        convert_file(p, "pdf")


def test_convert_many_reports_errors_without_raising(png, tmp_path):
    missing = tmp_path / "nope.png"
    results = convert_many([png, missing], "jpg", jobs=2)
    assert [r.ok for r in results] == [True, False]
    assert results[1].error


def test_parallel_conversions_get_unique_names(png, tmp_path):
    copies = []
    for i in range(6):
        d = tmp_path / f"d{i}"
        d.mkdir()
        c = d / "same.png"
        c.write_bytes(png.read_bytes())
        copies.append(c)
    out = tmp_path / "all"
    results = convert_many(copies, "jpg", out_dir=out, jobs=6)
    names = sorted(p.name for r in results for p in r.outputs)
    assert len(set(names)) == 6 and all(r.ok for r in results)


def test_reserve_output_is_race_free(tmp_path):
    got: list[Path] = []
    lock = threading.Lock()

    def worker():
        p = reserve_output(tmp_path, "x.tar.gz")
        with lock:
            got.append(p)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({p.name for p in got}) == 20
    assert "x (1).tar.gz" in {p.name for p in got}


@pytest.mark.parametrize("bad", ["../x", "a/b", "", ".."])
def test_reserve_output_rejects_paths(tmp_path, bad):
    with pytest.raises(Exception):
        reserve_output(tmp_path, bad)


def test_plugin_entry_point_loading(monkeypatch):
    import convertly.registry as registry_mod

    calls = []

    class EP:
        name = "demo"

        def load(self):
            return lambda reg: calls.append(reg) or reg.add("foo", "bar", lambda a, b, o: None)

    monkeypatch.setattr("importlib.metadata.entry_points", lambda group: [EP()])
    reg = Registry()
    registry_mod._load_plugins(reg)
    assert calls and reg.edges_from("foo")


def test_no_nonsense_routes():
    # an image is never "converted" to text through a PDF, a still image never becomes audio/video
    png_targets = targets("png")
    assert not png_targets & {"txt", "docx", "md", "mp3", "mp4"}
    assert "png" not in targets("mp4")
    assert "mp3" not in targets("gif")
    with pytest.raises(NoRoute):
        find_route("png", "mp3")
