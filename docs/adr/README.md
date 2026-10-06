# Architecture Decision Records

One short file per significant decision: **Context · Decision · Alternatives · Consequences**.
Do not write ADRs for trivial choices. To change a decision, add a new ADR that supersedes the old one.

| # | Decision |
| --- | --- |
| [0001](0001-modular-monolith-hexagonal.md) | Modular monolith with hexagonal architecture |
| [0002](0002-pyside6.md) | PySide6 as the UI adapter |
| [0003](0003-uv-and-pyproject.md) | uv + standards-based `pyproject.toml` (not Poetry) |
| [0004](0004-configuration.md) | Layered configuration, validated with pydantic |
| [0005](0005-logging.md) | Standard-library logging with a thin structured wrapper |
| [0006](0006-concurrency.md) | Thread-pool jobs + Qt dispatcher; no asyncio for now |
| [0007](0007-persistence.md) | Repository ports; stdlib `sqlite3`; Alembic/SQLAlchemy deferred |
| [0008](0008-error-handling.md) | Error taxonomy, `Result` for expected outcomes, one error boundary |
| [0009](0009-architecture-tests.md) | Custom AST architecture tests instead of import-linter |
| [0010](0010-ui-extension-points.md) | UI contributed through a registry; `module.py` may import Qt |
