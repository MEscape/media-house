"""A separate OS process that runs one reconciliation job against the same database as the test.

Run as ``python -m tests.support.reconcile_worker DATABASE PRESENT_FILE [options]``.
``PRESENT_FILE`` lists, one per line, the asset ids the "library" has; every other endpoint is
absent. The last line of stdout is the job's outcome as JSON.

* ``--batch N``              claims per batch (default 5)
* ``--crash-after-batches N`` hard exit (``os._exit(3)``) once N batches are committed
* ``--crash-after-claims N``  hard exit right after the N-th claim was applied, before its batch's
                              cursor moved: a crash in the middle of a batch
* ``--no-force``              respect the minimum interval between sweeps

A hard exit runs no cleanup, like a killed process, so the test sees exactly what survived.
"""

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from media_house.modules.asset_relationships.application.reconciliation import (
    ReconcileCommand,
    ReconcileRelationships,
    ReconciliationSettings,
)
from media_house.modules.asset_relationships.domain.reconciliation import ClaimVerdict
from media_house.modules.asset_relationships.domain.relationship import AssetRelationship
from media_house.modules.asset_relationships.infrastructure.sqlite_reconciliation_store import (
    SqliteReconciliationStore,
)
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaNotFound,
)
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Err, Ok, Result
from tests.support.relationship_fakes import original


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class PresentLibrary:
    """The part of the library that reconciliation reads, over a fixed set of present ids."""

    def __init__(self, present: set[str]) -> None:
        self._present = present

    def get(self, asset_id: str) -> Result[MediaAssetDto, MediaError]:
        if asset_id in self._present:
            return Ok(original(asset_id))
        return Err(MediaNotFound(asset_id))

    def get_many(self, asset_ids: Sequence[str]) -> list[MediaAssetDto]:
        return [original(i) for i in dict.fromkeys(asset_ids) if i in self._present]


class CrashingProgress:
    def __init__(self, after_batches: int) -> None:
        self._after = after_batches
        self._seen = 0

    def report(self, current: int, total: int | None = None, message: str = "") -> None:
        self._seen += 1
        if self._after and self._seen >= self._after:
            os._exit(3)


class CrashingStore(SqliteReconciliationStore):
    """Exits for real after the N-th claim was applied (and committed)."""

    def __init__(self, path: Path, after_claims: int) -> None:
        super().__init__(path)
        self._after = after_claims
        self._applied = 0

    def reconcile_claim(
        self,
        relationship_id: str,
        decide: Callable[[AssetRelationship], ClaimVerdict],
        *,
        now: datetime,
    ) -> ClaimVerdict | None:
        verdict = super().reconcile_claim(relationship_id, decide, now=now)
        self._applied += 1
        if self._after and self._applied >= self._after:
            os._exit(3)
        return verdict


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("present_file")
    parser.add_argument("--batch", type=int, default=5)
    parser.add_argument("--crash-after-batches", type=int, default=0)
    parser.add_argument("--crash-after-claims", type=int, default=0)
    parser.add_argument("--no-force", action="store_true")
    args = parser.parse_args(argv)

    present = {line for line in Path(args.present_file).read_text("utf-8").split() if line}
    database = Path(args.database)
    store = (
        CrashingStore(database, args.crash_after_claims)
        if args.crash_after_claims
        else SqliteReconciliationStore(database)
    )
    job = ReconcileRelationships(
        store,
        PresentLibrary(present),
        SystemClock(),
        (),
        ReconciliationSettings(
            batch_claims=args.batch,
            retry_delays_seconds=(0.0,),
            suspicious_recheck_seconds=0.0,
            suspicious_min_claims=10**6,
        ),
    )
    ctx = JobContext("worker", CancellationToken(), CrashingProgress(args.crash_after_batches))
    outcome = job.reconcile(ReconcileCommand(force=not args.no_force), ctx)
    sys.stdout.write(outcome.to_json() + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
