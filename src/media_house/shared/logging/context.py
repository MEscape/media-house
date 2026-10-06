"""Correlation / operation identifiers carried implicitly through a call chain.

``contextvars`` follow async/await and are copied explicitly into worker threads
by the job scheduler, so one user action can be traced across threads.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_operation_id: ContextVar[str | None] = ContextVar("operation_id", default=None)


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:12]


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def current_operation_id() -> str | None:
    return _operation_id.get()


@contextmanager
def bind_context(
    *,
    correlation_id: str | None = None,
    operation_id: str | None = None,
) -> Iterator[None]:
    """Bind ids for the duration of the block; unspecified ids are left untouched."""
    tokens = []
    if correlation_id is not None:
        tokens.append((_correlation_id, _correlation_id.set(correlation_id)))
    if operation_id is not None:
        tokens.append((_operation_id, _operation_id.set(operation_id)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
