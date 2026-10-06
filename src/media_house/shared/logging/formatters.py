"""Formatters and filters. Pure functions of a ``LogRecord``."""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from media_house.shared.logging.api import FIELDS_ATTR
from media_house.shared.logging.context import current_correlation_id, current_operation_id

_SENSITIVE = re.compile(
    r"pass(word|wd)?|secret|token|api[_-]?key|authorization|credential|cookie",
    re.IGNORECASE,
)
REDACTED = "***"


def redact(value: Any, key: str = "") -> Any:
    """Mask values whose *key* looks sensitive. Recurses into dicts."""
    if key and _SENSITIVE.search(key):
        return REDACTED
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    return value


class ContextFilter(logging.Filter):
    """Stamps correlation/operation ids and sanitised fields onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = current_correlation_id()
        record.operation_id = current_operation_id()
        raw = getattr(record, FIELDS_ATTR, None) or {}
        setattr(record, FIELDS_ATTR, redact(raw))
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line; ideal for files and log shippers."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "thread": record.threadName,
            "correlation_id": getattr(record, "correlation_id", None),
            "operation_id": getattr(record, "operation_id", None),
        }
        fields = getattr(record, FIELDS_ATTR, None)
        if fields:
            payload["fields"] = fields
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Human-friendly single-line output for development."""

    def format(self, record: logging.LogRecord) -> str:
        time = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]  # noqa: DTZ006 - local time for humans
        parts = [f"{time} {record.levelname:<7} {record.name} | {record.getMessage()}"]
        context = [
            f"{label}={value}"
            for label, value in (
                ("corr", getattr(record, "correlation_id", None)),
                ("op", getattr(record, "operation_id", None)),
            )
            if value
        ]
        fields = getattr(record, FIELDS_ATTR, None) or {}
        context += [f"{k}={v}" for k, v in fields.items() if v is not None]
        if context:
            parts.append("[" + " ".join(context) + "]")
        line = " ".join(parts)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line
