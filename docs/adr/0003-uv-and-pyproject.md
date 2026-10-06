# ADR-0003: uv with standards-based pyproject.toml (not Poetry)

Status: accepted

**Context.** Need reproducible installs, dev dependency groups, building a wheel, and one-command onboarding.

**Decision.** All metadata lives in standard `pyproject.toml` (PEP 621 + PEP 735 `dependency-groups`), built with
`hatchling`, resolved/locked/run with **uv** (`uv.lock`). uv also provisions the required Python version.
Runtime dependencies: PySide6-Essentials (UI), pydantic (validating *external* data), platformdirs (OS paths).
Dev: pytest, pytest-qt, mypy, ruff, pre-commit. Nothing else.

**Alternatives.** Poetry: mature, but uses non-standard metadata for much of its config, and its main extra
value (locking, groups, publishing) is covered by uv with standard files. pip-tools/venv: more manual steps.

**Consequences.** + `uv sync && uv run ...` is the whole onboarding. + Standard files keep us tool-portable.
- Contributors must install uv. - uv/PEP 735 are younger than Poetry; revisit if they cause friction.
