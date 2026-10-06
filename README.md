# Media-House

A modular desktop platform for media automation. This repository currently contains the
**architectural foundation** only: no media functionality yet, one small example module
(`workspace`) that shows where code belongs.

* Python 3.13+, PySide6 (Qt 6) UI
* Modular monolith · Hexagonal (ports & adapters) · pragmatic DDD · dependency rule enforced by tests

Read **[ARCHITECTURE.md](ARCHITECTURE.md)** first, then [CONTRIBUTING.md](CONTRIBUTING.md).
Decisions and their trade-offs are in [docs/adr](docs/adr/README.md).

## Quick start

Requires [uv](https://docs.astral.sh/uv/) (it also installs the right Python for you).

```bash
git clone <repo-url> media-house && cd media-house
uv sync                                   # creates .venv from uv.lock (runtime + dev tools)

uv run python -m media_house              # launch the desktop app
uv run python -m media_house --check      # headless startup self-test (no window); exit code 0/2
uv run python -m media_house --version
uv run python -m media_house --home ./.dev-data --environment development   # isolated dev data + console logs
```

`--home DIR` (or `MEDIA_HOUSE_HOME=DIR`) keeps config, data, cache and logs under one directory.
Without it the OS-conventional locations are used (via `platformdirs`).

## Developer commands

| Task | Command |
| --- | --- |
| **All quality gates** (what CI runs) | `uv run python scripts/check.py` |
| Same, auto-fixing format/lint first | `uv run python scripts/check.py --fix` |
| Install | `uv sync` |
| Run | `uv run python -m media_house` |
| Test (all) | `uv run pytest` |
| Test (fast: unit + architecture) | `uv run pytest tests/unit tests/architecture` |
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| Type-check (strict) | `uv run mypy` |
| Build wheel/sdist | `uv build` |
| Git hooks (optional) | `uv run pre-commit install` |

Headless machines/CI: tests run Qt with the `offscreen` platform automatically
(`tests/conftest.py`); no display server is needed.

## Configuration

Layers, lowest to highest precedence: built-in defaults → per-environment defaults →
`<config dir>/settings.toml` → `MEDIA_HOUSE_*` environment variables → command-line flags.

```toml
# settings.toml
[logging]
level = "DEBUG"

[ui]
theme = "dark"          # system | light | dark
```

```bash
MEDIA_HOUSE_ENVIRONMENT=development       # development | test | production (default)
MEDIA_HOUSE_LOGGING__LEVEL=DEBUG          # "__" separates nesting
MEDIA_HOUSE_CONCURRENCY__MAX_WORKERS=8
MEDIA_HOUSE_SECRET_SOME_API_KEY=...       # secrets: environment only, never settings.toml
```

Unknown keys and invalid values stop startup with a message and an error reference.
Logs are JSON lines in `<log dir>/media-house.log` (rotating); `development` also logs to the console.

## Layout (short)

```text
src/media_house/
  bootstrap/      composition root: CLI, headless Application, Qt desktop launch
  shared/         small cross-cutting kernel: errors, logging, config, paths, jobs, events
  core/           ports + adapters not owned by a feature (ProcessRunner, Clock), module contract
  modules/        feature modules (one example: workspace)
  presentation/   the application shell: main window, theme, job bridge, error boundary
tests/            unit · integration · architecture · presentation
docs/adr/         architecture decision records
```
