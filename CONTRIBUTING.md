# Contributing

Thanks for helping! Small, focused pull requests are easiest to review.

1. Fork, then `pip install -e ".[dev]"`.
2. Add or change code **with tests** (`tests/`). Engine tests use the `ffmpeg`, `libreoffice` or `pandoc`
   markers and skip automatically when the engine is missing.
3. Run `pytest` and `ruff check src tests && ruff format --check src tests`.
4. Open a PR against `main`. CI must be green; `main` only accepts squash-merged PRs (no force pushes).

New formats: see [docs/ADDING-A-CONVERTER.md](docs/ADDING-A-CONVERTER.md). Anything that parses untrusted
input should come with a test for its hostile case (see `SECURITY.md`).

By contributing you agree your work is released under the MIT license and that you follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
