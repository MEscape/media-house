# Contributing

## Workflow

1. `uv sync`
2. Make a focused change, with tests.
3. `uv run python scripts/check.py` must pass (format, lint, strict types, all tests).
4. Open a PR. If you changed an architectural rule, add or update an ADR in `docs/adr/`.

## Where does my code go?

| I am writing… | It goes in… |
| --- | --- |
| A business rule or invariant | `modules/<m>/domain/` (pure Python) |
| A user action / workflow | `modules/<m>/application/` as a use case class |
| Data crossing into the UI | a `dto` in `modules/<m>/application/` |
| Something that talks to disk / DB / a tool | `modules/<m>/infrastructure/`, behind a port |
| A widget / view model | `modules/<m>/presentation/` |
| A capability several modules need (e.g. run a process) | a port in `core/application/ports/`, adapter in `core/infrastructure/` |
| Wiring (which adapter implements which port) | `modules/<m>/module.py` or `bootstrap/` – nowhere else |
| A tiny primitive used by *every* layer | `shared/` – very rarely; see rules below |

If you cannot decide, ask in the PR. Putting it in `shared/` "because it's used twice" is almost always wrong.

## Adding a feature module

1. Copy the *shape* of `modules/workspace/`; drop layers you do not need.
2. Write the domain first, with unit tests that need no Qt, DB or filesystem.
3. Define repository/other ports in the domain or `application`; implement adapters in `infrastructure`.
4. Expose a **deliberately small** public API in `application/contracts.py` if other modules need you.
5. Write `module.py`: register services in the `Container`; add a `UiContributor` for UI.
6. Add one line to `bootstrap/modules.py`.
7. Do **not** edit the main window, other modules, or `shared/`.

## Rules the tests enforce (`tests/architecture`)

* `domain` imports only the standard library + `shared.errors|events|types` – no Qt, logging, sqlite, os, subprocess.
* `application` imports domain + ports – no infrastructure, presentation, Qt, sqlite, subprocess.
* `presentation` imports the application layer – **never** domain or infrastructure.
* Modules never import each other's internals; only `modules.<other>.application.contracts`.
* `subprocess` appears only in `core/infrastructure/process/subprocess_runner.py`.
* The `Container` is composition-only (`bootstrap`, `module.py`) – never injected into business code.
* Absolute imports only; no import cycles; unrecognised top-level packages and new `shared/` sub-packages fail.

Ruff additionally bans `os.environ`/`os.getenv` outside the CLI bootstrap and `print()` in library code.

## Style

* Strict typing (`mypy --strict`). Public functions are fully annotated. Avoid `Any`; justify every `# type: ignore[code]`.
* Prefer constructor injection and small classes. No module-level mutable state.
* Expected failures (bad input, duplicates) → return `Err(...)`. Unexpected failures → raise typed errors
  (`shared.errors`), always `raise X(...) from exc`. Never `except Exception: pass`.
* Never log secrets. Never show raw exceptions to users: use `ErrorHandler` → `ErrorReport`.
* UI strings live in a `strings.py`; stylesheets live in `presentation/resources/styles`.
* Never block the UI thread: use `UiJobRunner` for anything slower than a few milliseconds.

## Tests

* Unit tests (`tests/unit`) must not need Qt, files, network or tools. Use the fakes in `tests/support/fakes.py`.
* Integration tests (`tests/integration`) may use `tmp_path`, SQLite and child processes.
* Presentation tests use `qtbot` (offscreen). **Never call `QApplication.exec()` in-process**: run such
  checks in a subprocess (see `tests/presentation/test_desktop.py`).
* Warnings are errors (`filterwarnings = error`): fix leaks (unclosed connections, un-joined threads) instead of ignoring.
* A new port implementation should pass the same contract tests as existing ones
  (see `tests/integration/test_workspace_repository.py`).
