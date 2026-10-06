# ADR-0007: Repository ports; stdlib sqlite3; Alembic/SQLAlchemy deferred

Status: accepted

**Context.** A desktop app wants an embedded database eventually, but the schema is unknown and the foundation
must not lock into an ORM.

**Decision.** Persistence sits behind repository ports (domain-level `Protocol`s). The example adapter uses stdlib
`sqlite3`, one short-lived connection per operation (thread-safe by construction), `PRAGMA user_version` for the
schema version, unique constraint for business uniqueness, domain objects mapped explicitly (no ORM entities).
Adapters are verified by shared *contract tests* against the in-memory fake.

**Alternatives.** SQLAlchemy + Alembic now (powerful, but significant weight before there is a real schema);
JSON files (no constraints/transactions).

**Consequences.** + Zero dependencies, trivial to reason about. - Hand-written SQL and no migration tooling:
adopt Alembic (and possibly SQLAlchemy Core) when the first schema change is needed. Per-operation connections
are fine at this scale; revisit for bulk workloads.
