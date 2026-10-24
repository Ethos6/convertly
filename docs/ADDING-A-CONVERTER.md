# Adding a converter

A converter is a function `fn(src: Path, dst: Path, opts: Options) -> None | list[Path]`.
It reads `src`, writes `dst` (a path inside a private temp folder), and may return a list of files it wrote
when it produces several (then register it with `terminal=True`).

```python
# src/convertly/converters/myformat.py
from ..registry import Options, Registry

def foo_to_json(src, dst, opts: Options):
    dst.write_text(parse_foo(src.read_bytes()))

def register(reg: Registry) -> None:
    reg.add("foo", "json", foo_to_json, engine="builtin", cost=1.0)
```

Then add the module to `converters/__init__.py`, the format id to `formats.py` (category + magic bytes if it has
any), and tests to `tests/test_converters.py`.

Edge flags:

| Flag | Meaning |
|---|---|
| `cost` | Higher = less preferred. Use > 1 for lossy or slow paths |
| `terminal=True` | Only as the last step (multi-file output, or a result that makes no sense to convert further) |
| `direct=True` | Only as the first step (the original must really be this format) |
| `available=callable` | Hide the edge when an engine/library is missing |

Rules: never invoke a shell, always pass `timeout=opts.timeout` to `run_engine`, and treat input as hostile.
