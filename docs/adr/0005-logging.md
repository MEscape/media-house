# ADR-0005: Standard-library logging with a thin structured wrapper

Status: accepted

**Context.** Need levels, rotating files, JSON, correlation ids, redaction, and an API modules can use trivially.

**Decision.** Stdlib `logging` on the `media_house` logger (isolated from the root logger). `get_logger(__name__)`
returns a `StructuredLogger` accepting `log.info("msg", key=value)`. A filter adds `correlation_id`/`operation_id`
from `contextvars` and redacts sensitive field names; `JsonFormatter` writes the rotating file, `ConsoleFormatter`
serves development. The job scheduler copies the caller's context to workers so ids follow work across threads.

**Alternatives.** structlog/loguru: nicer ergonomics, but an extra dependency for features we can do in ~150 lines
on top of the standard ecosystem (handlers, third-party integrations).

**Consequences.** + No dependency, familiar handlers. - Redaction is key-name based: free-text messages are not
scanned, so *do not put secrets in messages* (also stated in CONTRIBUTING).
