"""Builders for tests that talk to the proxy store directly (no use case in between)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from media_house.modules.proxy_manager.application.ports import PublishOutcome
from media_house.modules.proxy_manager.domain.attempt import Attempt, AttemptState
from media_house.modules.proxy_manager.domain.records import ArtifactRecord
from media_house.modules.proxy_manager.domain.values import Evidence, Verdict
from media_house.modules.proxy_manager.infrastructure.sqlite_store import SqliteProxyStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FP = "a" * 64
LEASE = 120.0


def later(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


def attempt(
    n: int, *, fp: str = FP, source: str = "s", profile: str = "edit", **changes: Any
) -> Attempt:
    base = Attempt(
        id=f"a{n}",
        source_asset_id=source,
        profile=profile,
        profile_version=1,
        spec_fingerprint=fp,
        owner_token="me",
        created_at=NOW,
    )
    return replace(base, **changes)


def begin(store: SqliteProxyStore, n: int, **kw: Any) -> Attempt:
    return store.begin_attempt(
        attempt(n, **kw), free_bytes=10**12, margin_bytes=0, now=NOW, lease_seconds=LEASE
    )


def begin_as(
    store: SqliteProxyStore, n: int, *, at: float = 0, owner: str = "me", **kw: Any
) -> Attempt:
    """Begin an attempt of ``owner`` at ``at`` seconds after NOW (its heartbeat is that moment)."""
    started = replace(attempt(n, **kw), owner_token=owner, heartbeat_at=later(at))
    return store.begin_attempt(
        started, free_bytes=10**12, margin_bytes=0, now=later(at), lease_seconds=LEASE
    )


def other_owner(db: Path) -> SqliteProxyStore:
    return SqliteProxyStore(db, owner_token="other")


def publishing(store: SqliteProxyStore, started: Attempt) -> Attempt:
    current = started
    for state in (AttemptState.GENERATING, AttemptState.VALIDATING, AttemptState.PUBLISHING):
        current = current.advance(state, now=NOW)
        store.save_attempt(current)
    return current


def artifact(
    asset: str, owner: Attempt, *, verdict: Verdict = Verdict.VALID, fp: str = FP
) -> ArtifactRecord:
    return ArtifactRecord(
        asset_id=asset,
        source_asset_id=owner.source_asset_id,
        profile=owner.profile,
        spec_fingerprint=fp,
        attempt_id=owner.id,
        verdict=verdict,
        evidence=Evidence.PACKETS,
        validator_version=1,
        validated_at=NOW,
    )


def activate(
    store: SqliteProxyStore, n: int, asset: str, fp: str
) -> tuple[Attempt, PublishOutcome]:
    ready = publishing(store, begin(store, n, fp=fp))
    return store.publish(ready, artifact(asset, ready, fp=fp), now=NOW)
