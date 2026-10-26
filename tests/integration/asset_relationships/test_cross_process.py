"""Real operating-system processes create the same claim at once: one row, nobody fails.

Each worker is a separate Python process (``tests/support/relationship_worker.py``) with its own
connections, so database creation, migration and ``BEGIN IMMEDIATE`` are exercised across processes
(the in-process threads of ``test_repository.py`` share one interpreter).

What this does NOT prove: a network file system, or machines with skewed clocks.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from media_house.modules.asset_relationships.domain.repository import RelationshipCriteria
from media_house.modules.asset_relationships.infrastructure.sqlite_repository import (
    SqliteRelationshipRepository,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
PROCESSES = 4


def test_racing_processes_converge_on_one_relationship(tmp_path: Path) -> None:
    database = tmp_path / "asset_relationships.db"
    workers = [
        subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "tests.support.relationship_worker", str(database), "a", "b"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(PROCESSES)
    ]
    outcomes: list[str] = []
    for worker in workers:
        stdout, stderr = worker.communicate(timeout=90)
        assert worker.returncode == 0, stderr[-800:]
        outcomes.append(stdout.strip())

    assert sorted(outcomes) == ["created"] + ["existing"] * (PROCESSES - 1)
    assert len(SqliteRelationshipRepository(database).find(RelationshipCriteria("a"))) == 1
