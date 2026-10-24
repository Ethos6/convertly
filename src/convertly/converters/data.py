"""Structured data: JSON, JSON Lines, YAML, TOML, XML, CSV, TSV.

Everything is loaded into plain Python values and dumped again. XML is parsed with
``defusedxml`` (no entity expansion, no external entities) and YAML with ``safe_load``.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from ..registry import Registry

FORMATS = ("json", "jsonl", "yaml", "toml", "xml", "csv", "tsv")
MAX_BYTES = 512 * 1024**2


def _read_text(path: Path) -> str:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("file too large for in-memory data conversion")
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if enc == "utf-16" and raw[:2] not in (b"\xff\xfe", b"\xfe\xff"):
            continue
        return text
    return raw.decode("cp1252", "replace")


# ---------- loading ----------


def _xml_to_obj(elem) -> Any:
    node: dict[str, Any] = {f"@{k}": v for k, v in elem.attrib.items()}
    children = list(elem)
    for child in children:
        val = _xml_to_obj(child)
        tag = _strip_ns(child.tag)
        if tag in node:
            if not isinstance(node[tag], list):
                node[tag] = [node[tag]]
            node[tag].append(val)
        else:
            node[tag] = val
    text = (elem.text or "").strip()
    if text:
        if not node:
            return text
        node["#text"] = text
    return node if node else None


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _read_delimited(text: str, delim: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delim)
    rows = []
    for row in reader:
        rows.append({(k if k is not None else "_extra"): v for k, v in row.items()})
    return rows


def load(fmt: str, path: Path) -> Any:
    text = _read_text(path)
    if fmt == "json":
        return json.loads(text)
    if fmt == "jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if fmt == "yaml":
        import yaml

        docs = list(yaml.safe_load_all(text))
        return docs[0] if len(docs) == 1 else docs
    if fmt == "toml":
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - Python 3.10
            import tomli as tomllib
        return tomllib.loads(text)
    if fmt == "xml":
        from defusedxml import ElementTree

        root = ElementTree.fromstring(text.encode("utf-8"))
        return {_strip_ns(root.tag): _xml_to_obj(root)}
    if fmt in ("csv", "tsv"):
        return _read_delimited(text, "," if fmt == "csv" else "\t")
    raise ValueError(f"unknown data format {fmt}")


# ---------- dumping ----------


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)) and v:
                out.update(flatten(v, key))
            else:
                out[key] = v
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = f"{prefix}.{i}" if prefix else str(i)
            if isinstance(v, (dict, list)) and v:
                out.update(flatten(v, key))
            else:
                out[key] = v
    else:
        out[prefix or "value"] = obj
    return out


def to_rows(obj: Any) -> list[dict[str, Any]]:
    """Turn any JSON-like value into table rows."""
    if isinstance(obj, dict):
        lists = [v for v in obj.values() if isinstance(v, list)]
        if len(obj) == 1 and lists:
            obj = lists[0]
        else:
            return [flatten(obj)]
    if isinstance(obj, list):
        return [flatten(x) if isinstance(x, (dict, list)) else {"value": x} for x in obj]
    return [{"value": obj}]


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


_FORMULA = re.compile(r"^[=+\-@\t\r]")


def write_rows(rows: list[dict], dst: Path, delim: str, *, escape_formulas: bool = True) -> None:
    headers: list[str] = []
    for row in rows:
        for k in row:
            if k not in headers:
                headers.append(k)
    with open(dst, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=delim)
        w.writerow(headers)
        for row in rows:
            cells = [_cell(row.get(h)) for h in headers]
            if escape_formulas:  # CSV injection guard for spreadsheet apps
                cells = [("'" + c) if _FORMULA.match(c) and not _is_number(c) else c for c in cells]
            w.writerow(cells)


def _is_number(s: str) -> bool:
    try:
        float(s)
    except ValueError:
        return False
    return True


def _toml_clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _toml_clean(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_toml_clean(v) for v in obj if v is not None]
    return obj


def _xml_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(name)) or "item"
    if not re.match(r"[A-Za-z_]", name) or name.lower().startswith("xml"):
        name = "_" + name
    return name


def _obj_to_xml(parent, tag: str, obj: Any) -> None:
    from xml.etree.ElementTree import SubElement

    if isinstance(obj, list):
        for item in obj:
            _obj_to_xml(parent, tag, item)
        return
    el = SubElement(parent, _xml_name(tag))
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.startswith("@") and not isinstance(v, (dict, list)):
                el.set(_xml_name(k[1:]), _cell(v))
            elif k == "#text":
                el.text = _cell(v)
            else:
                _obj_to_xml(el, k, v)
    elif obj is not None:
        el.text = _cell(obj)


def dump(fmt: str, obj: Any, dst: Path) -> None:
    if fmt == "json":
        dst.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    elif fmt == "jsonl":
        items = obj if isinstance(obj, list) else [obj]
        dst.write_text("".join(json.dumps(i, ensure_ascii=False, default=str) + "\n" for i in items), encoding="utf-8")
    elif fmt == "yaml":
        import yaml

        dst.write_text(yaml.safe_dump(_jsonable(obj), allow_unicode=True, sort_keys=False), encoding="utf-8")
    elif fmt == "toml":
        import tomli_w

        data = _toml_clean(_jsonable(obj))
        if not isinstance(data, dict):
            data = {"items": data}
        dst.write_text(tomli_w.dumps(data), encoding="utf-8")
    elif fmt == "xml":
        from xml.etree.ElementTree import Element, ElementTree, indent

        if isinstance(obj, dict) and len(obj) == 1 and not isinstance(next(iter(obj.values())), list):
            ((tag, body),) = obj.items()
            holder = Element("holder")
            _obj_to_xml(holder, tag, body)
            root = holder[0]
        else:
            root = Element("root")
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _obj_to_xml(root, k, v)
            else:
                _obj_to_xml(root, "item", obj)
        indent(root)
        ElementTree(root).write(dst, encoding="utf-8", xml_declaration=True)
    elif fmt in ("csv", "tsv"):
        write_rows(to_rows(obj), dst, "," if fmt == "csv" else "\t")
    else:
        raise ValueError(f"unknown data format {fmt}")


def _jsonable(obj: Any) -> Any:
    """Round-trip through JSON so dates etc. become strings for YAML/TOML writers."""
    return json.loads(json.dumps(obj, default=str))


def register(reg: Registry) -> None:
    for s in FORMATS:
        for d in FORMATS:
            if s != d:
                cost = 1.0 if d not in ("csv", "tsv") or s in ("csv", "tsv") else 1.3
                reg.add(s, d, (lambda a_, b_: lambda a, b, o: dump(b_, load(a_, a), b))(s, d), engine="data", cost=cost)
