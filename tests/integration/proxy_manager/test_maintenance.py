"""Removing and pruning proxies: what is deleted, and above all what is never deleted."""

import sqlite3
from contextlib import closing
from typing import Any

import pytest

from media_house.modules.media_library.application.contracts import AssetRole
from media_house.modules.proxy_manager.application.contracts import (
    AttemptInFlight,
    Condition,
)
from media_house.modules.proxy_manager.domain.values import PROXY_OPERATION, SPEC_VERSION
from media_house.shared.errors import Err, Ok
from tests.integration.proxy_manager.conftest import Env
from tests.support.proxy_fakes import Gate, in_thread, source_facts
from tests.support.proxy_media import cfr_with_audio

pytestmark = pytest.mark.integration

FACTS = source_facts()
ORIGINAL_SPECS = (480, 320, 240)


def three_proxies(env: Env, source: str, use_case: Any) -> list[str]:
    """Proxies of three specs in submission order: the last is served, the one before is the
    rollback target and the first is referenced by nothing."""
    return [env.ok(use_case, source, max_long_edge=edge).proxy_asset_id for edge in ORIGINAL_SPECS]


def mark_pruning(env: Env, asset_id: str) -> None:
    with closing(sqlite3.connect(env.paths.data_dir / "proxy_manager.db")) as conn, conn:
        conn.execute("UPDATE artifacts SET state = 'pruning' WHERE asset_id = ?", (asset_id,))


def exists(env: Env, asset_id: str) -> bool:
    return isinstance(env.library.get(asset_id), Ok)


# ----------------------------------------------------------------------------------- prune
def test_prune_deletes_only_the_artifact_nothing_serves_or_keeps(env: Env) -> None:
    source = env.clip()
    use_case, _, _ = env.scripted(FACTS)
    oldest, rollback, served = three_proxies(env, source, use_case)

    report = env.maintenance.prune(dry_run=False)

    assert report.pruned == (oldest,) and report.kept == () and not report.dry_run
    assert not exists(env, oldest)
    assert exists(env, rollback) and exists(env, served) and exists(env, source)
    slot = env.store.get_slot(source, "analysis")
    assert slot is not None and (slot.active_asset_id, slot.previous_asset_id) == (served, rollback)
    assert env.store.get_artifact(oldest) is None
    assert env.catalog.condition(source, "analysis") is Condition.READY


def test_a_dry_run_reports_what_would_go_and_deletes_nothing(env: Env) -> None:
    source = env.clip()
    use_case, _, _ = env.scripted(FACTS)
    oldest, rollback, served = three_proxies(env, source, use_case)

    default = env.maintenance.prune()  # dry run is the default

    assert default.dry_run and default.pruned == (oldest,)
    assert all(exists(env, a) for a in (oldest, rollback, served))
    assert env.store.get_artifact(oldest) is not None


def test_pruning_twice_is_safe(env: Env) -> None:
    source = env.clip()
    use_case, _, _ = env.scripted(FACTS)
    three_proxies(env, source, use_case)
    env.maintenance.prune(dry_run=False)

    again = env.maintenance.prune(dry_run=False)

    assert again.pruned == () and again.kept == () and again.purged_records == 0


def test_prune_never_touches_the_original_or_other_modules_assets(env: Env) -> None:
    source = env.clip()
    env.inspect(source)  # an inspection document is a derived asset of the same source
    use_case, _, _ = env.scripted(FACTS)
    three_proxies(env, source, use_case)
    before = {a.id for a in env.library.list_derived(source)}

    env.maintenance.prune(dry_run=False)

    after = {a.id for a in env.library.list_derived(source)}
    inspections = [
        a.id
        for a in env.library.list_derived(source)
        if a.metadata.get("processing_type") != "proxy"
    ]
    assert exists(env, source) and inspections  # the inspection document survived
    assert len(before - after) == 1  # exactly the one unreferenced proxy


def test_prune_leaves_a_slot_with_a_request_in_flight_alone(env: Env) -> None:
    source = env.clip()
    gate = Gate(4)  # the fourth encode of this test is the one that is held
    use_case, _, _ = env.scripted(FACTS, on_encode=gate)
    oldest, _, _ = three_proxies(env, source, use_case)
    running, box = in_thread(lambda: env.ensure(use_case, source, max_long_edge=160))
    assert gate.inside.wait(30)

    held = env.maintenance.prune(dry_run=False)
    untouched = exists(env, oldest)
    gate.release.set()
    running.join(30)
    after = env.maintenance.prune(dry_run=False)

    assert held.pruned == () and untouched  # the slot was busy: nothing of it was touched
    assert isinstance(box["result"], Ok)
    assert len(after.pruned) == 2  # once the request ended, the two nobody keeps go


def test_an_artifact_with_derived_assets_is_kept_and_reported(env: Env) -> None:
    source = env.clip()
    use_case, _, _ = env.scripted(FACTS)
    oldest, _, _ = three_proxies(env, source, use_case)
    assert isinstance(env.library.ensure_thumbnail(oldest), Ok)  # something derives from it

    report = env.maintenance.prune(dry_run=False)

    assert report.pruned == () and [a for a, _ in report.kept] == [oldest]
    assert "derived" in report.kept[0][1] and exists(env, oldest)
    assert env.store.get_artifact(oldest) is not None  # back to live, not stuck half-deleted


def test_a_prune_that_stopped_half_way_is_finished_by_the_next_one(env: Env) -> None:
    source = env.clip()
    use_case, _, _ = env.scripted(FACTS)
    oldest, _, _ = three_proxies(env, source, use_case)
    mark_pruning(env, oldest)  # phase 1 done, phase 2 not: marked, the file is still there

    report = env.maintenance.prune(dry_run=False)

    assert report.pruned == (oldest,) and not exists(env, oldest)
    assert env.store.pruning_artifacts() == []


def test_records_of_things_the_library_cascaded_away_are_forgotten(env: Env) -> None:
    source = env.clip()
    use_case, _, _ = env.scripted(FACTS)
    three_proxies(env, source, use_case)
    assert isinstance(env.library.delete(source), Ok)  # cascades to every derived asset

    report = env.maintenance.prune(dry_run=False)

    assert report.purged_records == 3
    assert env.store.get_slot(source, "analysis") is None and env.store.list_artifacts() == []


def test_a_proxy_file_the_module_cannot_prove_it_made_is_reported_never_deleted(env: Env) -> None:
    source = env.clip()
    use_case, _, _ = env.scripted(FACTS)
    env.ok(use_case, source, max_long_edge=480)
    stray = env.library.register_derived(
        source,
        cfr_with_audio(env.media_dir / "stray.mp4", seconds=1),
        operation=PROXY_OPERATION,
        config={"somebody": "else"},
        version=SPEC_VERSION,
        metadata={"processing_type": PROXY_OPERATION},
        role=AssetRole.PREVIEW,
    )
    assert isinstance(stray, Ok)
    unknown_dir = env.paths.temp_dir / "proxy-nobody"
    unrelated_dir = env.paths.temp_dir / "somebody-elses"
    for directory in (unknown_dir, unrelated_dir):
        directory.mkdir(parents=True)

    report = env.maintenance.prune(dry_run=False)

    assert report.orphan_assets == (stray.value.asset.id,)
    assert report.orphan_scratch == ("proxy-nobody",)
    assert exists(env, stray.value.asset.id) and unknown_dir.is_dir() and unrelated_dir.is_dir()


# ----------------------------------------------------------------------------------- remove
def test_removing_a_slot_deletes_its_proxies_and_ensure_makes_a_fresh_one(env: Env) -> None:
    source = env.clip()
    use_case, encoder, _ = env.scripted(FACTS)
    oldest, rollback, served = three_proxies(env, source, use_case)

    result = env.maintenance.remove(source, "analysis")

    assert isinstance(result, Ok)
    assert set(result.value.removed_asset_ids) == {rollback, served} and result.value.kept == ()
    assert not exists(env, rollback) and not exists(env, served)
    assert exists(env, oldest) and exists(env, source)  # remove dissolves the slot only
    assert env.catalog.find(source, "analysis") is None
    assert env.catalog.condition(source, "analysis") is Condition.NONE
    assert env.store.get_slot(source, "analysis") is None
    again = env.ok(use_case, source, max_long_edge=240)
    assert not again.reused and len(encoder.calls) == 4


def test_removing_never_touches_the_original(env: Env) -> None:
    source = env.clip()
    original = env.library.get(source)
    use_case, _, _ = env.scripted(FACTS)
    env.ok(use_case, source)

    env.maintenance.remove(source, "analysis")

    assert env.library.get(source) == original


def test_removing_while_a_request_runs_is_refused_and_deletes_nothing(env: Env) -> None:
    source = env.clip()
    gate = Gate(2)
    use_case, _, _ = env.scripted(FACTS, on_encode=gate)
    first = env.ok(use_case, source, max_long_edge=480)
    running, box = in_thread(lambda: env.ensure(use_case, source, max_long_edge=320))
    assert gate.inside.wait(30)

    refused = env.maintenance.remove(source, "analysis")
    gate.release.set()
    running.join(30)

    assert isinstance(refused, Err) and isinstance(refused.error, AttemptInFlight)
    assert exists(env, first.proxy_asset_id) and isinstance(box["result"], Ok)


def test_removing_a_slot_nobody_made_is_a_quiet_no_op(env: Env) -> None:
    result = env.maintenance.remove(env.clip(), "analysis")

    assert isinstance(result, Ok) and result.value.removed_asset_ids == ()


def test_removal_keeps_a_proxy_that_other_assets_were_derived_from(env: Env) -> None:
    source = env.clip()
    use_case, _, _ = env.scripted(FACTS)
    dto = env.ok(use_case, source)
    assert isinstance(env.library.ensure_thumbnail(dto.proxy_asset_id), Ok)

    result = env.maintenance.remove(source, "analysis")

    assert isinstance(result, Ok) and result.value.removed_asset_ids == ()
    assert [a for a, _ in result.value.kept] == [dto.proxy_asset_id]
    assert exists(env, dto.proxy_asset_id)
    assert env.catalog.find(source, "analysis") is None  # out of service either way


def test_pruning_and_removing_with_real_ffmpeg_leave_playable_proxies(env: Env) -> None:
    source = env.import_media(cfr_with_audio(env.media_dir / "hd.mp4", seconds=2, size="1280x720"))
    for edge in (480, 320, 240):
        env.ok(env.real, source, "analysis", max_long_edge=edge)

    pruned = env.maintenance.prune(dry_run=False)

    assert len(pruned.pruned) == 1
    served = env.catalog.find(source, "analysis")
    assert served is not None and served.condition is Condition.READY
    again = env.ok(env.real, source, "analysis", max_long_edge=240)
    assert again.reused and again.proxy_asset_id == served.proxy_asset_id
