"""XLSX without Excel (openpyxl). Formula-looking text is always written as text."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..registry import Options, Registry
from .data import dump, load, to_rows

TABLES = ("csv", "tsv", "json", "jsonl", "yaml", "xml", "toml")


def _sheet_rows(ws) -> list[dict[str, Any]]:
    it = ws.iter_rows(values_only=True)
    header = next(it, None)
    if header is None:
        return []
    keys = []
    for i, h in enumerate(header):
        k = str(h).strip() if h not in (None, "") else f"column{i + 1}"
        while k in keys:
            k += "_"
        keys.append(k)
    rows = []
    for row in it:
        if row is None or all(v in (None, "") for v in row):
            continue
        rows.append({k: _plain(v) for k, v in zip(keys, row, strict=False)})
    return rows


def _plain(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def xlsx_load(src: Path, opts: Options) -> Any:
    import openpyxl

    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    try:
        if opts.sheet:
            if opts.sheet not in wb.sheetnames:
                raise ValueError(f"sheet {opts.sheet!r} not found; sheets: {', '.join(wb.sheetnames)}")
            return _sheet_rows(wb[opts.sheet])
        if len(wb.sheetnames) == 1:
            return _sheet_rows(wb[wb.sheetnames[0]])
        return {name: _sheet_rows(wb[name]) for name in wb.sheetnames}
    finally:
        wb.close()


def _number(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s or s != v or (len(s) > 1 and s[0] == "0" and s[1].isdigit()):
        return v  # keep leading zeros (zip codes, IDs) and whitespace as text
    try:
        return int(s) if s.lstrip("-").isdigit() and len(s) < 16 else float(s) if "." in s else v
    except ValueError:
        return v


def xlsx_dump(obj: Any, dst: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    sheets = (
        obj
        if isinstance(obj, dict) and obj and all(isinstance(v, list) for v in obj.values()) and len(obj) > 1
        else {"Sheet1": obj}
    )
    first = True
    for name, data in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = str(name)[:31].translate(str.maketrans("[]:*?/\\", "_______")) or "Sheet"
        rows = to_rows(data)
        headers: list[str] = []
        for r in rows:
            headers.extend(k for k in r if k not in headers)
        ws.append(headers)
        for r in rows:
            values = []
            for h in headers:
                v = r.get(h)
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                values.append(_number(v))
            ws.append(values)
            for cell in ws[ws.max_row]:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    cell.data_type = "s"  # never let input text become a formula
    wb.save(dst)


def register(reg: Registry) -> None:
    for fmt in TABLES:
        reg.add("xlsx", fmt, (lambda f: lambda a, b, o: dump(f, xlsx_load(a, o), b))(fmt), engine="openpyxl")
        reg.add(fmt, "xlsx", (lambda f: lambda a, b, o: xlsx_dump(load(f, a), b))(fmt), engine="openpyxl")
