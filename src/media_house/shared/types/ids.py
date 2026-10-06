"""Identifier generation."""

import uuid


def new_id() -> str:
    """Opaque, globally unique identifier (32 hex chars)."""
    return uuid.uuid4().hex
