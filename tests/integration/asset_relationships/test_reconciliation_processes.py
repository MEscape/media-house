"""Crash recovery and rival jobs across REAL operating-system processes.

Each worker (``tests/support/reconcile_worker.py``) is a separate Python process with its own
connections. A crash is a hard ``os._exit``: no cleanup, no ``finally``, exactly what a killed
process leaves behind. What survives must resume to the same end state as an uninterrupted run.

What this does NOT prove: a network file system, or machines with skewed clocks.
"""

import json
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.relationship import (
    AssetRelationship,
    ClaimInput,
)
from media_house.modules.asset_relationships.domain.values import (
    RelationshipOrigin,
    RelationshipType,
)
from media_house.modules.asset_relationships.infrastructure.sqlite_reconciliation_store import (
    SqliteReconciliationStore,
)
from media_house.modules.asset_relationships.infrastructure.sqlite_repository import (
    SqliteRelationshipRepository,
)
from tests.support.relationship_fakes import T0, tag_match

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
CLAIMS = 40
LOST = {3, 11, 12, 27, 38}  # claims whose first endpoint the library does not have


def seed(database: Path) -> None:
    repository = SqliteRelationshipRepository(database)
    for number in range(CLAIMS):
        claim = AssetRelationship.create(
            ClaimInput.of(
                RelationshipType.ALTERNATE_TAKE_OF,
                f"x{number}",
                f"y{number}",
                origin=RelationshipOrigin.DETECTOR,
                basis=Basis("take-detector", "1"),
                evidence=(tag_match(),),
            ),
            now=T0,
        )
        repository.update(claim.id, lambda _existing, c=claim: c)  # type: ignore[misc]


def present_file(directory: Path) -> Path:
    path = directory / "present.txt"
    ids = [f"x{n}" for n in range(CLAIMS) if n not in LOST] + [f"y{n}" for n in range(CLAIMS)]
    path.write_text("\n".join(ids), encoding="utf-8")
    return path


def spawn(database: Path, present: Path, *options: str) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "tests.support.reconcile_worker",
            str(database),
            str(present),
            *options,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def finish(process: subprocess.Popen[str], *, expect: int = 0) -> dict[str, Any]:
    stdout, stderr = process.communicate(timeout=120)
    assert process.returncode == expect, f"exit {process.returncode}: {stderr[-800:]}"
    return json.loads(stdout.strip().splitlines()[-1]) if expect == 0 else {}


def marked(database: Path) -> set[str]:
    with closing(sqlite3.connect(database)) as conn:
        return {
            r[0]
            for r in conn.execute(
                "SELECT relationship_id FROM relationships WHERE endpoint_missing_since IS NOT NULL"
            )
        }


@pytest.fixture
def reference(tmp_path: Path) -> tuple[Path, set[str]]:
    """The end state of one uninterrupted job, which every other run must reach."""
    database = tmp_path / "reference.db"
    seed(database)
    outcome = finish(spawn(database, present_file(tmp_path)))
    assert outcome["status"] == "completed"
    assert outcome["claims_checked"] == CLAIMS
    expected = marked(database)
    assert len(expected) == len(LOST)
    return database, expected


class TestCrashRecovery:
    @pytest.mark.parametrize("batches", ["2", "7"], ids=["after 2 batches", "after 7 batches"])
    def test_a_job_killed_between_batches_resumes_from_the_cursor_to_the_same_end_state(
        self, tmp_path: Path, reference: tuple[Path, set[str]], batches: str
    ) -> None:
        _reference_db, expected = reference
        database = tmp_path / "interrupted.db"
        seed(database)
        present = present_file(tmp_path)

        finish(spawn(database, present, "--crash-after-batches", batches), expect=3)
        state = SqliteReconciliationStore(database).load_state()
        assert state.claim_cursor is not None  # the sweep was in progress
        assert state.sweep_generation == 0
        assert state.last_run is None  # a killed process records nothing
        assert marked(database) <= expected  # whatever was applied is correct

        resumed = finish(spawn(database, present, "--no-force"))  # in progress: no interval wait

        assert resumed["status"] == "completed"
        assert resumed["claims_checked"] < CLAIMS  # it did not start over
        assert marked(database) == expected
        final = SqliteReconciliationStore(database).load_state()
        assert (final.claim_cursor, final.sweep_generation) == (None, 1)

    @pytest.mark.parametrize(
        "claims", ["1", "3", "5"], ids=["first claim", "third claim", "last claim"]
    )
    def test_a_job_killed_in_the_middle_of_a_batch_converges_to_the_same_end_state(
        self, tmp_path: Path, reference: tuple[Path, set[str]], claims: str
    ) -> None:
        """Only claims that need attention open a transaction; the kill comes right after one of
        them was applied and before its batch's cursor moved, so that batch is judged again."""
        _reference_db, expected = reference
        database = tmp_path / "interrupted.db"
        seed(database)
        present = present_file(tmp_path)

        finish(spawn(database, present, "--crash-after-claims", claims), expect=3)
        assert len(marked(database)) == int(claims)  # the applied claims are committed
        assert marked(database) <= expected

        resumed = finish(spawn(database, present, "--no-force"))

        assert resumed["status"] == "completed"
        assert marked(database) == expected
        assert SqliteReconciliationStore(database).load_state().sweep_generation == 1

    def test_a_second_run_after_recovery_writes_nothing(
        self, tmp_path: Path, reference: tuple[Path, set[str]]
    ) -> None:
        database = tmp_path / "interrupted.db"
        seed(database)
        present = present_file(tmp_path)
        finish(spawn(database, present, "--crash-after-batches", "3"), expect=3)
        finish(spawn(database, present, "--no-force"))

        again = finish(spawn(database, present))  # a forced, whole new sweep

        assert again["claims_checked"] == CLAIMS
        assert again["claims_changed"] == 0
        assert again["sweep_generation"] == 2

    def test_repeated_kills_still_converge(
        self, tmp_path: Path, reference: tuple[Path, set[str]]
    ) -> None:
        _reference_db, expected = reference
        database = tmp_path / "interrupted.db"
        seed(database)
        present = present_file(tmp_path)

        for _ in range(3):
            finish(spawn(database, present, "--crash-after-batches", "2", "--no-force"), expect=3)
        outcome = finish(spawn(database, present, "--no-force"))

        assert outcome["status"] == "completed"
        assert marked(database) == expected


class TestRivalJobs:
    def test_jobs_started_together_converge_on_one_end_state(
        self, tmp_path: Path, reference: tuple[Path, set[str]]
    ) -> None:
        _reference_db, expected = reference
        database = tmp_path / "rivals.db"
        seed(database)
        present = present_file(tmp_path)
        SqliteReconciliationStore(database).load_state()  # migrate once, as the app would

        workers = [spawn(database, present, "--batch", "4") for _ in range(4)]
        outcomes = [finish(w) for w in workers]

        assert {o["status"] for o in outcomes} <= {"completed", "superseded"}
        assert any(o["status"] == "completed" for o in outcomes)
        assert marked(database) == expected
        with closing(sqlite3.connect(database)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0] == CLAIMS

    def test_a_copy_of_the_reference_database_is_already_at_the_end_state(
        self, tmp_path: Path, reference: tuple[Path, set[str]]
    ) -> None:
        reference_db, expected = reference
        copy = tmp_path / "copy.db"
        shutil.copy(reference_db, copy)

        outcome = finish(spawn(copy, present_file(tmp_path)))

        assert marked(copy) == expected
        assert outcome["claims_changed"] == 0
