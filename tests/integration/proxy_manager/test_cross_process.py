"""Lease, takeover, waiting, fencing, pruning and migration across REAL operating-system processes.

Each worker is a separate Python process (``tests/support/proxy_worker.py``) sharing this test's
data directory: its own SQLite connections, its own clock, a real hard kill. Time is real, so the
lease is a few seconds and the margins are generous; nothing here sleeps for its own sake - every
wait is for a file or a database row, except where passing time IS the thing under test.

What this does NOT prove: processes on different machines or with skewed clocks, a network file
system, or the real FFmpeg encoder (the worker's encoder is scripted).
"""

import json
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from media_house.core.infrastructure.system_clock import SystemClock
from media_house.modules.proxy_manager.application.commands import EnsureProxyCommand
from media_house.modules.proxy_manager.application.contracts import (
    Condition,
    WaitTimedOut,
)
from media_house.modules.proxy_manager.application.ensure_proxy import EnsureProxy
from media_house.modules.proxy_manager.application.maintain_proxies import MaintainProxies
from media_house.modules.proxy_manager.application.ports import Scratch
from media_house.modules.proxy_manager.infrastructure.migrations import MIGRATIONS
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok
from tests.integration.proxy_manager.conftest import Env
from tests.support.proxy_fakes import CopyEncoder, FakeDisk, faithful_probe, source_facts
from tests.support.proxy_media import cfr_with_audio

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
LEASE = 3.0  # seconds; heartbeats come every 0.1 s, so a live owner is ~30x inside its lease
FACTS = source_facts()


class Workers:
    """Spawns worker processes for one test and makes sure none outlives it."""

    def __init__(self, env: Env) -> None:
        self.env = env
        self.root = env.media_dir.parent
        self._procs: list[subprocess.Popen[str]] = []
        self.files = self.root / "signals"
        self.files.mkdir(exist_ok=True)

    def path(self, name: str) -> Path:
        return self.files / name

    def spawn(self, command: str, source: str = "", **options: Any) -> subprocess.Popen[str]:
        args = [
            sys.executable,
            "-m",
            "tests.support.proxy_worker",
            command,
            "--root",
            str(self.root),
        ]
        if source:
            args += ["--source", source]
        for key, value in options.items():
            flag = "--" + key.replace("_", "-")
            if value is True:
                args.append(flag)
            elif value not in (False, None):
                args += [flag, str(value)]
        proc = subprocess.Popen(  # noqa: S603
            args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self._procs.append(proc)
        return proc

    def outcome(self, proc: subprocess.Popen[str], timeout: float = 90) -> dict[str, Any]:
        stdout, stderr = proc.communicate(timeout=timeout)
        assert proc.returncode == 0, f"worker exited {proc.returncode}: {stderr[-800:]}"
        parsed: dict[str, Any] = json.loads(stdout.strip().splitlines()[-1])
        return parsed

    def wait_for(self, path: Path, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while not path.exists():
            assert time.monotonic() < deadline, f"{path.name} never appeared"
            time.sleep(0.02)

    def close(self) -> None:
        for proc in self._procs:
            if proc.poll() is None:
                proc.kill()
            proc.communicate()


@pytest.fixture
def workers(env: Env) -> Iterator[Workers]:
    cfr_with_audio(env.media_dir / "template.mp4", seconds=1)  # what the workers' encoder copies
    group = Workers(env)
    try:
        yield group
    finally:
        group.close()


def parent_use_case(
    env: Env,
    *,
    lease: float = LEASE,
    wait_timeout: float = 30.0,
    on_encode: Callable[[int], None] | None = None,
) -> tuple[EnsureProxy, CopyEncoder]:
    encoder = CopyEncoder(env.media_dir / "template.mp4", on_encode)
    probe = faithful_probe(encoder)
    probe.facts = FACTS
    use_case = EnsureProxy(
        env.library, None, probe, encoder, env.store, FakeDisk(), env.container.resolve(Scratch),
        SystemClock(), lease_seconds=lease, heartbeat_seconds=0.1, poll_seconds=0.05,
        wait_timeout_seconds=wait_timeout,
    )  # fmt: skip
    return use_case, encoder


def run(use_case: EnsureProxy, source: str, **overrides: Any) -> Any:
    return use_case.execute(
        EnsureProxyCommand(source, "analysis", overrides), JobContext.detached()
    )


def maintenance(env: Env) -> MaintainProxies:
    return MaintainProxies(
        env.library, env.store, env.container.resolve(Scratch), SystemClock(), lease_seconds=LEASE
    )


def attempt_states(env: Env) -> list[tuple[str, str | None]]:
    return env.attempts()


# ----------------------------------------------------------------- heartbeat and takeover
def test_a_live_process_keeps_its_lease_and_a_killed_one_loses_it(
    env: Env, workers: Workers
) -> None:
    source = env.clip()
    holder = workers.spawn(
        "ensure", source, signal=workers.path("a-in"), release=workers.path("never"),
        lease=LEASE, heartbeat=0.1,
    )  # fmt: skip
    workers.wait_for(workers.path("a-in"))
    time.sleep(LEASE * 1.6)  # longer than the lease: only its heartbeat can keep the attempt alive

    impatient, _ = parent_use_case(env, wait_timeout=0.4)
    respected = run(impatient, source)

    assert isinstance(respected, Err) and isinstance(respected.error, WaitTimedOut)
    assert attempt_states(env) == [("generating", None)]  # alive after > 1.5 leases

    holder.kill()  # a hard kill: no cleanup of any kind
    holder.wait(30)
    taker, encoder = parent_use_case(env, wait_timeout=30)
    started = time.monotonic()
    result = run(taker, source)

    assert isinstance(result, Ok) and result.value.condition is Condition.READY
    assert len(encoder.calls) == 1
    assert time.monotonic() - started < LEASE + 5  # it waited out one lease, not forever
    rows = env.rows("SELECT state, failure, error_message FROM attempts ORDER BY seq")
    assert rows[0][:2] == ("interrupted", "transient") and "lease expired" in rows[0][2]
    assert rows[1][0] == "ready"
    sweeper, _ = parent_use_case(env)
    sweeper.recover()  # a fresh process sweeps what the dead one left behind
    assert env.scratch_dirs() == []


def test_a_crash_inside_the_encoder_leaves_nothing_servable_and_is_recovered(
    env: Env, workers: Workers
) -> None:
    source = env.clip()
    crashed = workers.spawn(
        "ensure", source, signal=workers.path("a-in"), crash=True, lease=LEASE, heartbeat=0.1
    )
    crashed.communicate(timeout=90)
    assert crashed.returncode == 9  # it really died inside the encoder, no JSON, no cleanup
    assert env.catalog.find(source, "analysis") is None
    assert env.attempts() == [("generating", None)] and env.scratch_dirs() != []

    taker, encoder = parent_use_case(env, wait_timeout=30)
    result = run(taker, source)

    assert isinstance(result, Ok) and len(encoder.calls) == 1
    parent_use_case(env)[0].recover()
    assert env.scratch_dirs() == [] and env.catalog.condition(source, "analysis") is Condition.READY


# ---------------------------------------------------- takeover racing the original owner
def test_an_owner_that_was_retired_while_still_running_stops_and_changes_nothing(
    env: Env, workers: Workers
) -> None:
    """Alive but silent (no heartbeat): another process takes over, then the first one wakes up."""
    source = env.clip()
    silent = workers.spawn(
        "ensure", source, signal=workers.path("a-in"), release=workers.path("a-go"),
        lease=LEASE, heartbeat=3600,
    )  # fmt: skip
    workers.wait_for(workers.path("a-in"))
    time.sleep(LEASE + 0.5)  # its lease runs out while it is still "working"

    taker, encoder = parent_use_case(env)
    taken = run(taker, source)
    workers.path("a-go").touch()  # now the original owner continues
    woke = workers.outcome(silent)

    assert isinstance(taken, Ok) and len(encoder.calls) == 1
    assert woke["status"] == "err" and woke["error"] == "LeaseLost", woke
    served = env.catalog.find(source, "analysis")
    assert served is not None and served.proxy_asset_id == taken.value.proxy_asset_id
    assert len(env.library.list_derived(source)) == 1  # the retired owner registered nothing
    states = [s for s, _ in env.attempts()]
    assert states == ["interrupted", "ready"]
    parent_use_case(env)[0].recover()
    assert env.scratch_dirs() == []


def test_requests_in_different_processes_obey_the_same_activation_fencing(
    env: Env, workers: Workers
) -> None:
    source = env.clip()
    older = workers.spawn(
        "ensure", source, edge=480, signal=workers.path("a-in"), release=workers.path("a-go"),
        lease=LEASE, heartbeat=0.1,
    )  # fmt: skip
    workers.wait_for(workers.path("a-in"))
    newer_use_case, _ = parent_use_case(env)
    newer = run(newer_use_case, source, max_long_edge=320)  # submitted later, finishes first
    workers.path("a-go").touch()
    late = workers.outcome(older)

    assert isinstance(newer, Ok)
    assert late["status"] == "err" and late["error"] == "Superseded"
    served = env.catalog.find(source, "analysis")
    assert served is not None and served.proxy_asset_id == newer.value.proxy_asset_id
    assert len(env.store.list_artifacts()) == 2  # the older result was kept, just not served
    assert sorted(s for s, _ in env.attempts()) == ["ready", "superseded"]


# ------------------------------------------------------------------------------ waiting
def test_identical_requests_from_three_processes_encode_once_and_share_the_result(
    env: Env, workers: Workers
) -> None:
    source = env.clip()
    procs = [
        workers.spawn(
            "ensure",
            source,
            ready=workers.path(f"r{n}"),
            start=workers.path("go"),
            sleep=1.5,
            lease=LEASE,
            heartbeat=0.1,
            wait_timeout=60,
        )
        for n in range(3)
    ]
    for n in range(3):
        workers.wait_for(workers.path(f"r{n}"))
    workers.path("go").touch()  # all three ask at the same moment

    outcomes = [workers.outcome(p) for p in procs]

    assert [o["status"] for o in outcomes] == ["ok", "ok", "ok"], outcomes
    assert len({o["proxy"] for o in outcomes}) == 1
    assert sum(o["encoder_calls"] for o in outcomes) == 1  # one encode for the three of them
    assert sum(1 for o in outcomes if not o["reused"]) == 1
    assert sorted(s for s, _ in env.attempts()) == ["ready"]
    assert len(env.library.list_derived(source)) == 1


def test_a_waiter_in_another_process_learns_that_the_request_it_waited_for_died(
    env: Env, workers: Workers
) -> None:
    source = env.clip()
    leader = workers.spawn(
        "ensure", source, signal=workers.path("a-in"), release=workers.path("never"),
        lease=LEASE, heartbeat=0.1,
    )  # fmt: skip
    workers.wait_for(workers.path("a-in"))
    waiter = workers.spawn("ensure", source, lease=LEASE, heartbeat=0.1, wait_timeout=30)
    time.sleep(1.5)  # let the waiter reach its wait

    leader.kill()
    outcome = workers.outcome(waiter)

    # the dead owner's attempt is retired after its lease, and the waiter takes over and finishes
    assert outcome["status"] == "ok" and outcome["encoder_calls"] == 1
    assert env.attempts()[0][0] == "interrupted" and env.attempts()[-1][0] == "ready"


# ---------------------------------------------------------------- pruning and removal
def test_prune_and_remove_respect_a_live_process_and_stop_waiting_for_a_dead_one(
    env: Env, workers: Workers
) -> None:
    source = env.clip()
    use_case, _ = parent_use_case(env)
    oldest = run(use_case, source, max_long_edge=480).value.proxy_asset_id
    run(use_case, source, max_long_edge=320)
    run(use_case, source, max_long_edge=240)
    busy = workers.spawn(
        "ensure", source, edge=160, signal=workers.path("a-in"), release=workers.path("never"),
        lease=LEASE, heartbeat=0.1,
    )  # fmt: skip
    workers.wait_for(workers.path("a-in"))
    time.sleep(LEASE * 1.2)  # past one lease: only the live heartbeat protects the slot

    pruned_while_alive = maintenance(env).prune(dry_run=False)
    removed_while_alive = maintenance(env).remove(source, "analysis")
    survives = isinstance(env.library.get(oldest), Ok)
    busy.kill()
    busy.wait(30)
    time.sleep(LEASE + 0.5)  # its silence now exceeds the lease
    pruned_after = maintenance(env).prune(dry_run=False)

    assert pruned_while_alive.pruned == () and survives
    assert isinstance(removed_while_alive, Err)  # AttemptInFlight: a live process owns the slot
    assert pruned_after.pruned == (oldest,)


# ---------------------------------------------------------------------------- migration
def make_slice_1_database(env: Env) -> Path:
    """A database exactly as Slice 1 wrote it: schema version 1, with a served proxy in it."""
    path = env.paths.data_dir / "proxy_manager.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = "a" * 64
    with closing(sqlite3.connect(path)) as conn, conn:
        for statement in MIGRATIONS[0].statements:
            conn.execute(statement)
        conn.execute("PRAGMA user_version = 1")
        conn.execute(
            "INSERT INTO attempts (id, source_asset_id, profile, profile_version, "
            "spec_fingerprint, state, owner_token, artifact_asset_id, created_at, finished_at) "
            "VALUES ('s1', 'src', 'edit', 1, ?, 'ready', 'old-process', 'A1', "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:01+00:00')",
            (fingerprint,),
        )
        conn.execute(
            "INSERT INTO artifacts VALUES ('A1', 'src', 'edit', ?, 's1', 'valid', 'packets', "
            "1, '2026-01-01T00:00:01+00:00', 'live')",
            (fingerprint,),
        )
        conn.execute(
            "INSERT INTO slots VALUES ('src', 'edit', 'A1', NULL, "
            "(SELECT seq FROM attempts WHERE id = 's1'), '2026-01-01T00:00:01+00:00')"
        )
    return path


def test_four_processes_opening_a_slice_1_database_at_once_migrate_it_exactly_once(
    env: Env, workers: Workers
) -> None:
    db = make_slice_1_database(env)
    procs = [
        workers.spawn("migrate", ready=workers.path(f"m{n}"), start=workers.path("go"))
        for n in range(4)
    ]
    for n in range(4):
        workers.wait_for(workers.path(f"m{n}"))
    workers.path("go").touch()

    outcomes = [workers.outcome(p) for p in procs]

    assert all(o == {"status": "ok"} for o in outcomes)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        columns = [r[1] for r in conn.execute("PRAGMA table_info(attempts)")]
        assert columns.count("heartbeat_at") == 1
        assert conn.execute("SELECT active_asset_id FROM slots").fetchall() == [("A1",)]


def test_four_processes_creating_a_brand_new_database_at_once_all_succeed(
    env: Env, workers: Workers
) -> None:
    db = env.paths.data_dir / "proxy_manager.db"
    assert not db.exists()
    procs = [
        workers.spawn("migrate", ready=workers.path(f"n{n}"), start=workers.path("go"))
        for n in range(4)
    ]
    for n in range(4):
        workers.wait_for(workers.path(f"n{n}"))
    workers.path("go").touch()

    outcomes = [workers.outcome(p) for p in procs]

    assert all(o == {"status": "ok"} for o in outcomes), outcomes
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_a_slice_1_database_keeps_serving_and_accepts_new_work_after_the_upgrade(
    env: Env,
) -> None:
    make_slice_1_database(env)
    cfr_with_audio(env.media_dir / "template.mp4", seconds=1)
    source = env.clip()
    use_case, _ = parent_use_case(env)

    slot = env.store.get_slot("src", "edit")  # Slice 1's own slot is readable after the upgrade
    first = run(use_case, source)
    removed = maintenance(env).remove("src", "edit")

    assert slot is not None and slot.active_asset_id == "A1"
    assert isinstance(first, Ok) and first.value.condition is Condition.READY
    assert isinstance(removed, Ok) and env.store.get_slot("src", "edit") is None
    # Slice 1's record of an asset this library never had is forgotten, not an error
    assert removed.value.removed_asset_ids == ("A1",) and env.store.get_artifact("A1") is None
    pruned = maintenance(env).prune(dry_run=False)
    assert pruned.pruned == () and pruned.kept == ()
