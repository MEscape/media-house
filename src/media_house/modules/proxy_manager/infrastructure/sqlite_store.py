# ruff: noqa: S608 - SQL text is joined only from fixed fragments and the state-name constants.
"""SQLite implementation of :class:`ProxyStore`: slots, artifacts and attempts in one small file.

One short-lived connection per operation (thread-safe by construction, ADR-0007). Every write is
one ``BEGIN IMMEDIATE`` transaction, so concurrent writers queue instead of interleaving; the
fencing and reservation rules below run inside such a transaction and are therefore race-free
across threads AND processes.
"""

import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import closing, suppress
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from media_house.modules.proxy_manager.application.ports import PublishOutcome
from media_house.modules.proxy_manager.domain.attempt import (
    ACTIVE_STATES,
    Attempt,
    AttemptState,
    FailureKind,
)
from media_house.modules.proxy_manager.domain.errors import (
    AttemptInFlight,
    InsufficientSpace,
    LeaseLost,
)
from media_house.modules.proxy_manager.domain.records import ArtifactRecord, ArtifactState, Slot
from media_house.modules.proxy_manager.domain.values import Evidence, Verdict
from media_house.modules.proxy_manager.infrastructure.migrations import LATEST_VERSION, MIGRATIONS
from media_house.shared.errors import InvariantViolation, PersistenceError

_BUSY_TIMEOUT_SECONDS = 15.0
_RETRY_SECONDS = 0.05
_RETRY_WINDOW_SECONDS = 5.0
#: What SQLite says while another connection is switching modes or recovering the write-ahead log
#: (after a process was killed, Windows reports the recovery as a transient I/O error).
_TRANSIENT_MESSAGES = ("locked", "disk i/o error")


def _transient(exc: sqlite3.Error) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and any(
        m in str(exc).lower() for m in _TRANSIENT_MESSAGES
    )


_ACTIVE = tuple(s.value for s in ACTIVE_STATES)
_ACTIVE_SQL = "(" + ", ".join(f"'{s}'" for s in _ACTIVE) + ")"


def _ts(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SqliteProxyStore:
    """Structurally implements ``ProxyStore``."""

    def __init__(self, path: Path, *, owner_token: str) -> None:
        self._path = path
        self._owner = owner_token
        self._ready = False
        self._lock = threading.Lock()

    @property
    def owner_token(self) -> str:
        return self._owner

    # ------------------------------------------------------------------ attempts
    def begin_attempt(
        self,
        attempt: Attempt,
        *,
        free_bytes: int,
        margin_bytes: int,
        now: datetime,
        lease_seconds: float,
    ) -> Attempt:
        def work(conn: sqlite3.Connection) -> Attempt:
            running = conn.execute(
                "SELECT * FROM attempts WHERE source_asset_id = ? AND profile = ? "
                f"AND spec_fingerprint = ? AND state IN {_ACTIVE_SQL}",
                (attempt.source_asset_id, attempt.profile, attempt.spec_fingerprint),
            ).fetchone()
            if running is not None:
                if not self._lapsed(running, now, lease_seconds):
                    raise AttemptInFlight(running["id"])
                _update_attempt(conn, self._retire(running, now))
            reserved = 0
            if attempt.reserved_volume is not None:
                rows = conn.execute(
                    f"SELECT * FROM attempts WHERE reserved_volume = ? AND state IN {_ACTIVE_SQL}",
                    (attempt.reserved_volume,),
                ).fetchall()
                reserved = sum(
                    r["reserved_bytes"] for r in rows if not self._lapsed(r, now, lease_seconds)
                )
            available = free_bytes - reserved - margin_bytes
            if attempt.reserved_bytes > 0 and attempt.reserved_bytes > available:
                raise InsufficientSpace(attempt.reserved_bytes, max(0, available))
            cursor = conn.execute(_INSERT, _insert_params(attempt))
            return replace(attempt, seq=int(cursor.lastrowid or 0))

        return self._write(work)

    def touch_attempt(self, attempt_id: str, *, now: datetime) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "UPDATE attempts SET heartbeat_at = ? WHERE id = ? AND owner_token = ? "
                f"AND state IN {_ACTIVE_SQL}",
                (_ts(now), attempt_id, self._owner),
            )
            return cursor.rowcount > 0

        return self._write(work)

    def expire_lease(self, attempt_id: str, *, now: datetime, lease_seconds: float) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            row = conn.execute(
                f"SELECT * FROM attempts WHERE id = ? AND state IN {_ACTIVE_SQL}", (attempt_id,)
            ).fetchone()
            if row is None or not self._lapsed(row, now, lease_seconds):
                return False
            _update_attempt(conn, self._retire(row, now))
            return True

        return self._write(work)

    def _lapsed(self, row: sqlite3.Row, now: datetime, lease_seconds: float) -> bool:
        """Another owner's attempt that has shown no sign of life for longer than the lease.

        An attempt of THIS owner is never lapsed: its thread is alive or ended it itself.
        """
        if row["owner_token"] == self._owner:
            return False
        beat = _parse(row["heartbeat_at"]) or _parse(row["created_at"])
        return beat is not None and (now - beat).total_seconds() > lease_seconds

    @staticmethod
    def _retire(row: sqlite3.Row, now: datetime) -> Attempt:
        return _attempt(row).advance(
            AttemptState.INTERRUPTED,
            now=now,
            failure=FailureKind.TRANSIENT,
            error_message="the owner of this attempt stopped responding (lease expired)",
        )

    def save_attempt(self, attempt: Attempt) -> None:
        def work(conn: sqlite3.Connection) -> None:
            row = conn.execute("SELECT state FROM attempts WHERE id = ?", (attempt.id,)).fetchone()
            stored = AttemptState(row["state"]) if row is not None else None
            if stored is not None and stored.terminal and stored is not attempt.state:
                raise LeaseLost(attempt.id)  # retired (or ended) behind this owner's back
            _update_attempt(conn, attempt)

        self._write(work)

    def get_attempt(self, attempt_id: str) -> Attempt | None:
        def work(conn: sqlite3.Connection) -> Attempt | None:
            row = conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
            return _attempt(row) if row else None

        return self._read(work)

    def has_attempt_for(self, source_asset_id: str, spec_fingerprint: str) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            return (
                conn.execute(
                    "SELECT 1 FROM attempts WHERE source_asset_id = ? AND spec_fingerprint = ? "
                    "LIMIT 1",
                    (source_asset_id, spec_fingerprint),
                ).fetchone()
                is not None
            )

        return self._read(work)

    # ------------------------------------------------------------------ slots and artifacts
    def get_slot(self, source_asset_id: str, profile: str) -> Slot | None:
        def work(conn: sqlite3.Connection) -> Slot | None:
            row = conn.execute(
                "SELECT * FROM slots WHERE source_asset_id = ? AND profile = ?",
                (source_asset_id, profile),
            ).fetchone()
            return _slot(row) if row else None

        return self._read(work)

    def get_artifact(self, asset_id: str) -> ArtifactRecord | None:
        def work(conn: sqlite3.Connection) -> ArtifactRecord | None:
            row = conn.execute("SELECT * FROM artifacts WHERE asset_id = ?", (asset_id,)).fetchone()
            return _artifact(row) if row else None

        return self._read(work)

    def find_artifact(self, source_asset_id: str, spec_fingerprint: str) -> ArtifactRecord | None:
        def work(conn: sqlite3.Connection) -> ArtifactRecord | None:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE source_asset_id = ? AND spec_fingerprint = ?",
                (source_asset_id, spec_fingerprint),
            ).fetchone()
            return _artifact(row) if row else None

        return self._read(work)

    def update_verdict(
        self,
        asset_id: str,
        verdict: Verdict,
        evidence: Evidence,
        validator_version: int,
        *,
        now: datetime,
    ) -> None:
        def work(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE artifacts SET verdict = ?, evidence = ?, validator_version = ?, "
                "validated_at = ? WHERE asset_id = ?",
                (verdict.value, evidence.value, validator_version, _ts(now), asset_id),
            )

        self._write(work)

    def publish(
        self, attempt: Attempt, artifact: ArtifactRecord, *, now: datetime
    ) -> tuple[Attempt, PublishOutcome]:
        if attempt.state is not AttemptState.PUBLISHING:
            raise InvariantViolation("Only a publishing attempt can publish an artifact")

        def work(conn: sqlite3.Connection) -> tuple[Attempt, PublishOutcome]:
            current = conn.execute(
                "SELECT state FROM attempts WHERE id = ?", (attempt.id,)
            ).fetchone()
            if current is None or current["state"] != AttemptState.PUBLISHING.value:
                raise LeaseLost(attempt.id)  # nothing below may happen for a retired attempt
            held = conn.execute(
                "SELECT state FROM artifacts WHERE asset_id = ?", (artifact.asset_id,)
            ).fetchone()
            if held is not None and held["state"] == ArtifactState.PRUNING.value:
                return attempt, PublishOutcome.PRUNING
            # The replaced record and its slot reference move in one transaction.
            conn.execute("PRAGMA defer_foreign_keys = ON")
            replaced = attempt.replaces_asset_id
            if replaced is not None:
                conn.execute("DELETE FROM artifacts WHERE asset_id = ?", (replaced,))
            conn.execute(_UPSERT_ARTIFACT, _artifact_params(artifact))
            slot_row = conn.execute(
                "SELECT * FROM slots WHERE source_asset_id = ? AND profile = ?",
                (attempt.source_asset_id, attempt.profile),
            ).fetchone()
            slot = _slot(slot_row) if slot_row else None
            if slot is not None and slot.activated_seq >= attempt.seq:
                if replaced is not None:
                    conn.execute(
                        "UPDATE slots SET previous_asset_id = NULL WHERE source_asset_id = ? "
                        "AND profile = ? AND previous_asset_id = ?",
                        (attempt.source_asset_id, attempt.profile, replaced),
                    )
                done = attempt.advance(
                    AttemptState.SUPERSEDED, now=now, artifact_asset_id=artifact.asset_id
                )
                _update_attempt(conn, done)
                return done, PublishOutcome.SUPERSEDED
            previous = _next_previous(conn, slot, artifact.asset_id, replaced)
            if slot is None:
                conn.execute(
                    "INSERT INTO slots (source_asset_id, profile, active_asset_id, "
                    "previous_asset_id, activated_seq, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        attempt.source_asset_id,
                        attempt.profile,
                        artifact.asset_id,
                        previous,
                        attempt.seq,
                        _ts(now),
                    ),
                )
            else:
                conn.execute(
                    "UPDATE slots SET active_asset_id = ?, previous_asset_id = ?, "
                    "activated_seq = ?, updated_at = ? WHERE source_asset_id = ? AND profile = ?",
                    (
                        artifact.asset_id,
                        previous,
                        attempt.seq,
                        _ts(now),
                        attempt.source_asset_id,
                        attempt.profile,
                    ),
                )
            done = attempt.advance(AttemptState.READY, now=now, artifact_asset_id=artifact.asset_id)
            _update_attempt(conn, done)
            return done, PublishOutcome.ACTIVATED

        return self._write(work)

    # ------------------------------------------------------------------ recovery
    def interrupt_foreign_attempts(
        self, *, now: datetime, lease_seconds: float
    ) -> Sequence[Attempt]:
        def work(conn: sqlite3.Connection) -> list[Attempt]:
            rows = conn.execute(
                f"SELECT * FROM attempts WHERE state IN {_ACTIVE_SQL} AND owner_token <> ?",
                (self._owner,),
            ).fetchall()
            ended: list[Attempt] = []
            for row in rows:
                if self._lapsed(row, now, lease_seconds):
                    done = self._retire(row, now)
                    _update_attempt(conn, done)
                    ended.append(done)
            return ended

        return self._write(work)

    # ------------------------------------------------------------------ removing and pruning
    def list_artifacts(self) -> Sequence[ArtifactRecord]:
        def work(conn: sqlite3.Connection) -> list[ArtifactRecord]:
            return [_artifact(r) for r in conn.execute("SELECT * FROM artifacts ORDER BY asset_id")]

        return self._read(work)

    def unreferenced_artifacts(self) -> Sequence[ArtifactRecord]:
        def work(conn: sqlite3.Connection) -> list[ArtifactRecord]:
            return _unreferenced(conn)

        return self._read(work)

    def claim_unreferenced(self) -> Sequence[ArtifactRecord]:
        def work(conn: sqlite3.Connection) -> list[ArtifactRecord]:
            found = _unreferenced(conn)
            for record in found:
                conn.execute(
                    "UPDATE artifacts SET state = ? WHERE asset_id = ?",
                    (ArtifactState.PRUNING.value, record.asset_id),
                )
            return [replace(r, state=ArtifactState.PRUNING) for r in found]

        return self._write(work)

    def claim_slot(self, source_asset_id: str, profile: str) -> Sequence[ArtifactRecord]:
        def work(conn: sqlite3.Connection) -> list[ArtifactRecord]:
            running = conn.execute(
                "SELECT id FROM attempts WHERE source_asset_id = ? AND profile = ? "
                f"AND state IN {_ACTIVE_SQL} LIMIT 1",
                (source_asset_id, profile),
            ).fetchone()
            if running is not None:
                raise AttemptInFlight(running["id"])
            slot = conn.execute(
                "SELECT * FROM slots WHERE source_asset_id = ? AND profile = ?",
                (source_asset_id, profile),
            ).fetchone()
            if slot is None:
                return []
            held = [slot["active_asset_id"], slot["previous_asset_id"]]
            conn.execute(
                "DELETE FROM slots WHERE source_asset_id = ? AND profile = ?",
                (source_asset_id, profile),
            )
            claimed: list[ArtifactRecord] = []
            for asset_id in (a for a in held if a is not None):
                conn.execute(
                    "UPDATE artifacts SET state = ? WHERE asset_id = ?",
                    (ArtifactState.PRUNING.value, asset_id),
                )
                row = conn.execute(
                    "SELECT * FROM artifacts WHERE asset_id = ?", (asset_id,)
                ).fetchone()
                if row is not None:
                    claimed.append(_artifact(row))
            return claimed

        return self._write(work)

    def pruning_artifacts(self) -> Sequence[ArtifactRecord]:
        def work(conn: sqlite3.Connection) -> list[ArtifactRecord]:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE state = ? ORDER BY asset_id",
                (ArtifactState.PRUNING.value,),
            )
            return [_artifact(r) for r in rows]

        return self._read(work)

    def finish_prune(self, asset_id: str) -> None:
        def work(conn: sqlite3.Connection) -> None:
            conn.execute(
                "DELETE FROM artifacts WHERE asset_id = ? AND state = ?",
                (asset_id, ArtifactState.PRUNING.value),
            )

        self._write(work)

    def abandon_prune(self, asset_id: str) -> None:
        def work(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE artifacts SET state = ? WHERE asset_id = ? AND state = ?",
                (ArtifactState.LIVE.value, asset_id, ArtifactState.PRUNING.value),
            )

        self._write(work)

    def drop_record(self, asset_id: str) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "DELETE FROM artifacts WHERE asset_id = ? AND NOT EXISTS ("
                "SELECT 1 FROM slots WHERE active_asset_id = ? OR previous_asset_id = ?)",
                (asset_id, asset_id, asset_id),
            )
            return cursor.rowcount > 0

        return self._write(work)

    def purge_source(self, source_asset_id: str) -> int:
        def work(conn: sqlite3.Connection) -> int:
            conn.execute("DELETE FROM slots WHERE source_asset_id = ?", (source_asset_id,))
            cursor = conn.execute(
                "DELETE FROM artifacts WHERE source_asset_id = ?", (source_asset_id,)
            )
            return cursor.rowcount

        return self._write(work)

    def known_sources(self) -> Sequence[str]:
        def work(conn: sqlite3.Connection) -> list[str]:
            rows = conn.execute(
                "SELECT source_asset_id FROM artifacts UNION SELECT source_asset_id FROM attempts "
                "ORDER BY 1"
            )
            return [r[0] for r in rows]

        return self._read(work)

    def attempts_with_scratch(self) -> Sequence[Attempt]:
        def work(conn: sqlite3.Connection) -> list[Attempt]:
            rows = conn.execute(
                "SELECT * FROM attempts WHERE scratch_name IS NOT NULL "
                f"AND state NOT IN {_ACTIVE_SQL}"
            ).fetchall()
            return [_attempt(r) for r in rows]

        return self._read(work)

    def clear_scratch(self, attempt_id: str) -> None:
        def work(conn: sqlite3.Connection) -> None:
            conn.execute("UPDATE attempts SET scratch_name = NULL WHERE id = ?", (attempt_id,))

        self._write(work)

    # ------------------------------------------------------------------ connection handling
    def _read[T](self, work: Callable[[sqlite3.Connection], T]) -> T:
        deadline = time.monotonic() + _RETRY_WINDOW_SECONDS
        while True:
            try:
                self._ensure_schema()
                with closing(self._connect()) as conn:
                    return work(conn)
            except sqlite3.Error as exc:
                # a read is idempotent: a transient failure is simply tried again
                if _transient(exc) and time.monotonic() < deadline:
                    time.sleep(_RETRY_SECONDS)
                    continue
                raise self._wrap("read", exc) from exc

    def _write[T](self, work: Callable[[sqlite3.Connection], T]) -> T:
        """One immediate transaction. Only failures BEFORE ``work`` starts are retried: once it has
        run, an error (even at COMMIT) may or may not have taken effect, so it is never repeated."""
        deadline = time.monotonic() + _RETRY_WINDOW_SECONDS
        while True:
            try:
                self._ensure_schema()
                conn = self._connect()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                except sqlite3.Error:
                    conn.close()
                    raise
            except sqlite3.Error as exc:
                if _transient(exc) and time.monotonic() < deadline:
                    time.sleep(_RETRY_SECONDS)
                    continue
                raise self._wrap("write", exc) from exc
            try:
                with closing(conn):
                    return self._run_in_transaction(conn, work)
            except sqlite3.Error as exc:
                raise self._wrap("write", exc) from exc

    @staticmethod
    def _run_in_transaction[T](
        conn: sqlite3.Connection, work: Callable[[sqlite3.Connection], T]
    ) -> T:
        try:
            result = work(conn)
        except BaseException:
            with suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        return result

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=_BUSY_TIMEOUT_SECONDS, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            if self._ready:
                return
            deadline = time.monotonic() + _BUSY_TIMEOUT_SECONDS
            while True:
                try:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    with closing(self._connect()) as conn:
                        self._enable_wal(conn)
                        self._migrate(conn)
                    break
                except sqlite3.OperationalError as exc:
                    # Switching the journal mode needs the file to itself and SQLite does not wait
                    # for that like it waits for a write lock: another process opening the same
                    # new database at the same moment gets "locked". It is transient; retry.
                    if not _transient(exc) or time.monotonic() > deadline:
                        raise self._wrap("initialise", exc) from exc
                    time.sleep(_RETRY_SECONDS)
                except (OSError, sqlite3.Error) as exc:
                    raise self._wrap("initialise", exc) from exc
            self._ready = True

    @staticmethod
    def _enable_wal(conn: sqlite3.Connection) -> None:
        # reading the mode takes no lock; only a database not yet in WAL mode needs the switch
        if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
            conn.execute("PRAGMA journal_mode = WAL")

    def _migrate(self, conn: sqlite3.Connection) -> None:
        # IMMEDIATE + re-reading the version inside the transaction applies each migration once
        # even when several processes start together.
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current > LATEST_VERSION:
                raise PersistenceError(
                    "The proxy database was created by a newer version of Media-House",
                    details={"database": str(self._path), "version": current},
                )
            for migration in MIGRATIONS:
                if migration.version <= current:
                    continue
                for statement in migration.statements:
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {int(migration.version)}")
        except BaseException:
            with suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def _wrap(self, action: str, exc: sqlite3.Error | OSError) -> PersistenceError:
        return PersistenceError(
            f"Proxy database {action} failed: {exc}", details={"database": str(self._path)}
        )


# ---------------------------------------------------------------------- row mapping
_COLUMNS = (
    "id, source_asset_id, profile, profile_version, spec_fingerprint, state, owner_token, "
    "scratch_name, reserved_bytes, reserved_volume, replaces_asset_id, artifact_asset_id, "
    "encoder, fallback, toolchain, failure, error_message, created_at, finished_at, heartbeat_at"
)
_INSERT = f"INSERT INTO attempts ({_COLUMNS}) VALUES ({', '.join('?' * 20)})"
_UPSERT_ARTIFACT = (
    "INSERT INTO artifacts (asset_id, source_asset_id, profile, spec_fingerprint, attempt_id, "
    "verdict, evidence, validator_version, validated_at, state) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT (asset_id) DO UPDATE SET verdict = excluded.verdict, "
    "evidence = excluded.evidence, validator_version = excluded.validator_version, "
    "validated_at = excluded.validated_at"
)


def _insert_params(a: Attempt) -> tuple[object, ...]:
    return (
        a.id, a.source_asset_id, a.profile, a.profile_version, a.spec_fingerprint, a.state.value,
        a.owner_token, a.scratch_name, a.reserved_bytes, a.reserved_volume, a.replaces_asset_id,
        a.artifact_asset_id, a.encoder, a.fallback, a.toolchain,
        a.failure.value if a.failure else None, a.error_message, _ts(a.created_at),
        _ts(a.finished_at), _ts(a.heartbeat_at),
    )  # fmt: skip


def _update_attempt(conn: sqlite3.Connection, a: Attempt) -> None:
    cursor = conn.execute(
        "UPDATE attempts SET state = ?, scratch_name = ?, reserved_bytes = ?, reserved_volume = ?, "
        "replaces_asset_id = ?, artifact_asset_id = ?, encoder = ?, fallback = ?, toolchain = ?, "
        "failure = ?, error_message = ?, finished_at = ?, heartbeat_at = ? WHERE id = ?",
        (
            a.state.value, a.scratch_name, a.reserved_bytes, a.reserved_volume,
            a.replaces_asset_id, a.artifact_asset_id, a.encoder, a.fallback, a.toolchain,
            a.failure.value if a.failure else None, a.error_message, _ts(a.finished_at),
            _ts(a.heartbeat_at), a.id,
        ),
    )  # fmt: skip
    if cursor.rowcount == 0:
        raise PersistenceError("Attempt row is missing", details={"attempt_id": a.id})


def _unreferenced(conn: sqlite3.Connection) -> list[ArtifactRecord]:
    """Live artifacts nothing serves or keeps, whose slot has no attempt in flight."""
    rows = conn.execute(
        "SELECT a.* FROM artifacts a WHERE a.state = ? "
        "AND NOT EXISTS (SELECT 1 FROM slots s "
        "WHERE s.active_asset_id = a.asset_id OR s.previous_asset_id = a.asset_id) "
        "AND NOT EXISTS (SELECT 1 FROM attempts t WHERE t.source_asset_id = a.source_asset_id "
        f"AND t.profile = a.profile AND t.state IN {_ACTIVE_SQL}) ORDER BY a.asset_id",
        (ArtifactState.LIVE.value,),
    )
    return [_artifact(r) for r in rows]


def _next_previous(
    conn: sqlite3.Connection, slot: Slot | None, new_asset: str, replaced: str | None
) -> str | None:
    """The rollback target after activating ``new_asset``: the old active one if still usable."""
    if slot is None:
        return None
    old = slot.active_asset_id
    if old not in (new_asset, replaced):
        row = conn.execute("SELECT verdict FROM artifacts WHERE asset_id = ?", (old,)).fetchone()
        if row is not None and Verdict(row["verdict"]).usable:
            return old
    kept = slot.previous_asset_id
    return kept if kept not in (None, new_asset, replaced) else None


def _attempt(row: sqlite3.Row) -> Attempt:
    created = _parse(row["created_at"])
    assert created is not None  # noqa: S101 - NOT NULL column
    return Attempt(
        id=row["id"],
        source_asset_id=row["source_asset_id"],
        profile=row["profile"],
        profile_version=row["profile_version"],
        spec_fingerprint=row["spec_fingerprint"],
        owner_token=row["owner_token"],
        created_at=created,
        seq=row["seq"],
        state=AttemptState(row["state"]),
        scratch_name=row["scratch_name"],
        reserved_bytes=row["reserved_bytes"],
        reserved_volume=row["reserved_volume"],
        replaces_asset_id=row["replaces_asset_id"],
        artifact_asset_id=row["artifact_asset_id"],
        encoder=row["encoder"],
        fallback=row["fallback"],
        toolchain=row["toolchain"],
        failure=FailureKind(row["failure"]) if row["failure"] else None,
        error_message=row["error_message"],
        finished_at=_parse(row["finished_at"]),
        heartbeat_at=_parse(row["heartbeat_at"]),
    )


def _artifact_params(a: ArtifactRecord) -> tuple[object, ...]:
    return (
        a.asset_id, a.source_asset_id, a.profile, a.spec_fingerprint, a.attempt_id,
        a.verdict.value, a.evidence.value, a.validator_version, _ts(a.validated_at),
        a.state.value,
    )  # fmt: skip


def _artifact(row: sqlite3.Row) -> ArtifactRecord:
    validated = _parse(row["validated_at"])
    assert validated is not None  # noqa: S101 - NOT NULL column
    return ArtifactRecord(
        asset_id=row["asset_id"],
        source_asset_id=row["source_asset_id"],
        profile=row["profile"],
        spec_fingerprint=row["spec_fingerprint"],
        attempt_id=row["attempt_id"],
        verdict=Verdict(row["verdict"]),
        evidence=Evidence(row["evidence"]),
        validator_version=row["validator_version"],
        validated_at=validated,
        state=ArtifactState(row["state"]),
    )


def _slot(row: sqlite3.Row) -> Slot:
    updated = _parse(row["updated_at"])
    assert updated is not None  # noqa: S101 - NOT NULL column
    return Slot(
        source_asset_id=row["source_asset_id"],
        profile=row["profile"],
        active_asset_id=row["active_asset_id"],
        previous_asset_id=row["previous_asset_id"],
        activated_seq=row["activated_seq"],
        updated_at=updated,
    )
