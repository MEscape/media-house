"""Canonical digests: the same content always gives the same hash (no time, no run id).

The one implementation is ``core.domain.json_codec``; this name stays for the module's callers."""

from media_house.core.domain.json_codec import canon, digest

__all__ = ["canon", "digest"]
