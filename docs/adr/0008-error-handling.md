# ADR-0008: Error taxonomy, Result for expected outcomes, one error boundary

Status: accepted

**Context.** Users need actionable messages; developers need diagnostics; unexpected bugs must not kill the app.

**Decision.** All deliberate errors derive from `MediaHouseError` with a category (domain, validation,
application, infrastructure, external system, configuration, unexpected), a stable `code`, a user-safe
`user_message`, developer `details` and an `error_id`. *Expected* outcomes (invalid input, duplicate) are returned as
`Result = Ok | Err` from use cases; *unexpected* failures raise (always chained with `from`). One `ErrorHandler` logs
(traceback for non-expected categories) and returns an `ErrorReport` for the UI; a global boundary catches
uncaught exceptions in slots and threads and shows a dialog with the reference id.

**Alternatives.** Exceptions only (forces try/except for normal flow); a full functional Result library (heavy).

**Consequences.** + Clear split between business outcomes and bugs; support can match a dialog id to a log line.
- Two mechanisms to learn; the rule is stated in CONTRIBUTING.
