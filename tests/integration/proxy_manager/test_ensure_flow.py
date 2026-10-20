"""EnsureProxy over the REAL library and store with a scripted encoder and probe.

These tests prove the lifecycle rules (reuse, validation gate, fencing, repair, recovery,
cleanup) without depending on what FFmpeg does; ``test_ensure_real_media.py`` proves that.
"""

import errno
import hashlib
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.contracts import AssetRole, MediaLibrary
from media_house.modules.proxy_manager.application.commands import EnsureProxyCommand
from media_house.modules.proxy_manager.application.contracts import (
    ArtifactUnrepairable,
    Condition,
    Evidence,
    InsufficientSpace,
    OutputExceedsReservation,
    Superseded,
    Unverifiable,
    ValidationFailed,
    Verdict,
    read_proxy_provenance,
)
from media_house.modules.proxy_manager.application.ensure_proxy import EnsureProxy
from media_house.modules.proxy_manager.application.ports import Scratch
from media_house.modules.proxy_manager.domain.attempt import Attempt, AttemptState, FailureKind
from media_house.modules.proxy_manager.domain.profile import resolve_profile
from media_house.modules.proxy_manager.domain.spec import ResolvedSpec, resolve_spec
from media_house.modules.proxy_manager.domain.validation import OutputFacts
from media_house.modules.proxy_manager.domain.values import (
    PROXY_OPERATION,
    SPEC_VERSION,
    JsonValue,
)
from media_house.modules.proxy_manager.infrastructure.sqlite_store import SqliteProxyStore
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import (
    Err,
    InfrastructureError,
    NotFoundError,
    Ok,
    OperationCancelledError,
    ProcessFailedError,
    ValidationError,
)
from tests.integration.proxy_manager.conftest import Env
from tests.support.fakes import FixedClock
from tests.support.proxy_fakes import (
    CopyEncoder,
    FakeDisk,
    Gate,
    ScriptedProbe,
    in_thread,
    perfect_output,
    source_facts,
)
from tests.support.proxy_media import audio_only, cfr_with_audio

pytestmark = pytest.mark.integration

FACTS = source_facts()
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def clip(env: Env, name: str = "clip.mp4") -> str:
    return env.import_media(cfr_with_audio(env.media_dir / name))


def db_rows(env: Env, sql: str) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(env.paths.data_dir / "proxy_manager.db")) as conn:
        return [tuple(r) for r in conn.execute(sql)]


def attempts(env: Env) -> list[tuple[str, str | None]]:
    return db_rows(env, "SELECT state, failure FROM attempts ORDER BY seq")  # type: ignore[return-value]


def scratch_dirs(env: Env) -> list[str]:
    root = env.paths.temp_dir
    return (
        sorted(p.name for p in root.iterdir() if p.name.startswith("proxy-"))
        if root.exists()
        else []
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------- the first slice's promises
def test_first_request_generates_validates_registers_activates_and_serves(env: Env) -> None:
    source = clip(env)
    use_case, encoder, _ = env.scripted(FACTS)

    dto = env.ok(use_case, source)

    assert (dto.reused, dto.condition, dto.verdict, dto.evidence) == (
        False, Condition.READY, Verdict.VALID, Evidence.PACKETS,
    )  # fmt: skip
    assert dto.source_asset_id == source and dto.proxy_asset_id != source
    assert len(encoder.calls) == 1
    asset = env.library.get(dto.proxy_asset_id)
    assert isinstance(asset, Ok)
    assert asset.value.role is AssetRole.PREVIEW
    assert asset.value.derivation is not None
    assert (asset.value.derivation.source_asset_id, asset.value.derivation.operation) == (
        source, PROXY_OPERATION,
    )  # fmt: skip
    served = env.catalog.find(source, "analysis")
    assert served is not None and served.proxy_asset_id == dto.proxy_asset_id
    assert env.catalog.condition(source, "analysis") is Condition.READY


def test_the_proxy_describes_itself_for_other_modules(env: Env) -> None:
    source = clip(env)
    use_case, _, _ = env.scripted(FACTS)

    dto = env.ok(use_case, source)

    asset = env.library.get(dto.proxy_asset_id)
    assert isinstance(asset, Ok)
    provenance = read_proxy_provenance(asset.value.metadata)
    assert provenance is not None
    assert (
        provenance.source_asset_id == source and provenance.spec_fingerprint == dto.spec_fingerprint
    )
    assert provenance.encoder == "fake-encoder" and provenance.toolchain == "fake-ffmpeg 1"
    assert provenance.facts_origin == "probe" and provenance.verdict is Verdict.VALID


def test_proxies_stay_out_of_the_default_library_listing(env: Env) -> None:
    source = clip(env)
    use_case, _, _ = env.scripted(FACTS)

    dto = env.ok(use_case, source)

    listed = {a.id for a in env.library.search().items}
    assert source in listed and dto.proxy_asset_id not in listed
    assert dto.proxy_asset_id in {a.id for a in env.library.list_derived(source)}


def test_a_second_identical_request_is_served_without_encoding_or_writing(env: Env) -> None:
    source = clip(env)
    use_case, encoder, probe = env.scripted(FACTS)
    first = env.ok(use_case, source)
    before = db_rows(env, "SELECT * FROM attempts")

    second = env.ok(use_case, source)

    assert second.reused and second.proxy_asset_id == first.proxy_asset_id
    assert len(encoder.calls) == 1 and len(probe.output_probes) == 1
    assert db_rows(env, "SELECT * FROM attempts") == before  # a pure read: no attempt, no write


def test_the_original_is_never_modified_and_keeps_its_identity(env: Env) -> None:
    source = clip(env)
    original = env.library.local_path(source)
    assert isinstance(original, Ok)
    digest, record = sha256(original.value), env.library.get(source)
    use_case, _, _ = env.scripted(FACTS)

    env.ok(use_case, source, max_long_edge=480)
    env.ok(use_case, source, max_long_edge=320)

    after = env.library.get(source)
    assert isinstance(record, Ok) and isinstance(after, Ok)
    assert after.value == record.value  # same id, checksum, metadata
    assert sha256(original.value) == digest


def test_a_replacement_with_another_spec_is_served_and_the_old_one_stays_as_rollback(
    env: Env,
) -> None:
    source = clip(env)
    use_case, encoder, _ = env.scripted(FACTS)
    first = env.ok(use_case, source, max_long_edge=480)

    second = env.ok(use_case, source, max_long_edge=320)

    slot = env.store.get_slot(source, "analysis")
    assert second.proxy_asset_id != first.proxy_asset_id and second.source_asset_id == source
    assert slot is not None and (slot.active_asset_id, slot.previous_asset_id) == (
        second.proxy_asset_id, first.proxy_asset_id,
    )  # fmt: skip
    # asking for the earlier spec again activates the stored proxy: nothing is encoded
    again = env.ok(use_case, source, max_long_edge=480)
    assert again.reused and again.proxy_asset_id == first.proxy_asset_id
    assert len(encoder.calls) == 2


def test_two_profiles_are_two_independent_slots(env: Env) -> None:
    source = clip(env)
    use_case, _, _ = env.scripted(FACTS)

    analysis = env.ok(use_case, source, "analysis")
    edit = env.ok(use_case, source, "edit")

    assert analysis.proxy_asset_id != edit.proxy_asset_id
    assert (
        env.catalog.find(source, "analysis") is not None
        and env.catalog.find(source, "edit") is not None
    )


# --------------------------------------------------------------------------- failure paths
def test_generation_failure_keeps_the_previous_proxy_and_cleans_up(env: Env) -> None:
    source = clip(env)

    def boom(call: int) -> None:
        if call == 2:
            raise ProcessFailedError("ffmpeg", 1, "encoder crashed")

    use_case, _, _ = env.scripted(FACTS, on_encode=boom)
    first = env.ok(use_case, source, max_long_edge=480)

    with pytest.raises(ProcessFailedError):
        env.ensure(use_case, source, max_long_edge=320)

    served = env.catalog.find(source, "analysis")
    assert served is not None and served.proxy_asset_id == first.proxy_asset_id
    assert attempts(env)[-1] == ("failed", "transient")
    assert scratch_dirs(env) == []
    assert len(env.library.list_derived(source)) == 1  # no second proxy was registered


def test_validation_failure_publishes_nothing_and_keeps_the_previous_proxy(env: Env) -> None:
    source = clip(env)
    template = cfr_with_audio(env.media_dir / "t.mp4", seconds=1)
    encoder = CopyEncoder(template)

    def output(path: Path, decode: bool) -> OutputFacts:
        request = encoder.by_destination[path]
        good = perfect_output(request.spec, request.facts)
        if len(encoder.calls) < 2 or good.video is None:
            return good
        return replace(good, video=replace(good.video, frames=good.video.frames - 5))

    use_case = env.build(encoder, ScriptedProbe(FACTS, output))
    first = env.ok(use_case, source, max_long_edge=480)

    result = env.ensure(use_case, source, max_long_edge=320)

    assert isinstance(result, Err) and isinstance(result.error, ValidationFailed)
    assert "frame_count" in str(result.error)
    served = env.catalog.find(source, "analysis")
    assert served is not None and served.proxy_asset_id == first.proxy_asset_id
    assert attempts(env)[-1] == ("failed", "permanent")
    assert len(env.library.list_derived(source)) == 1
    assert scratch_dirs(env) == []


def test_output_that_cannot_be_fully_verified_is_not_published(env: Env) -> None:
    source = clip(env)
    template = cfr_with_audio(env.media_dir / "t.mp4", seconds=1)
    encoder = CopyEncoder(template)
    # the probe "forgets" to decode although decoding was required
    probe = ScriptedProbe(
        FACTS,
        lambda p, d: perfect_output(
            encoder.by_destination[p].spec, encoder.by_destination[p].facts
        ),
    )
    use_case = env.build(encoder, probe)

    result = env.ensure(use_case, source, full_decode_validation=True)

    assert isinstance(result, Err) and isinstance(result.error, Unverifiable)
    assert env.catalog.find(source, "analysis") is None
    assert env.library.list_derived(source) == []


def test_an_output_larger_than_its_reservation_is_discarded(env: Env) -> None:
    source = clip(env)
    template = cfr_with_audio(env.media_dir / "t.mp4", seconds=1)
    encoder = CopyEncoder(template)
    probe = ScriptedProbe(
        FACTS,
        lambda p, d: perfect_output(
            encoder.by_destination[p].spec, encoder.by_destination[p].facts, size=10**12
        ),
    )

    result = env.ensure(env.build(encoder, probe), source)

    assert isinstance(result, Err) and isinstance(result.error, OutputExceedsReservation)
    assert env.library.list_derived(source) == [] and scratch_dirs(env) == []


def test_cancellation_ends_the_attempt_keeps_the_previous_proxy_and_cleans_up(env: Env) -> None:
    source = clip(env)

    def cancel(call: int) -> None:
        if call == 2:
            raise OperationCancelledError("cancelled")

    use_case, _, _ = env.scripted(FACTS, on_encode=cancel)
    first = env.ok(use_case, source, max_long_edge=480)

    with pytest.raises(OperationCancelledError):
        env.ensure(use_case, source, max_long_edge=320)

    served = env.catalog.find(source, "analysis")
    assert served is not None and served.proxy_asset_id == first.proxy_asset_id
    assert attempts(env)[-1] == ("cancelled", "cancelled")
    assert scratch_dirs(env) == []


def test_not_enough_disk_is_refused_before_any_work_and_leaves_no_attempt(env: Env) -> None:
    source = clip(env)
    template = cfr_with_audio(env.media_dir / "t.mp4", seconds=1)
    encoder = CopyEncoder(template)
    probe = ScriptedProbe(FACTS, lambda p, d: pytest.fail("nothing may be probed or encoded"))
    use_case = env.build(encoder, probe, disk=FakeDisk(free=1000))

    result = env.ensure(use_case, source)

    assert isinstance(result, Err) and isinstance(result.error, InsufficientSpace)
    assert encoder.calls == [] and attempts(env) == []


def test_running_out_of_disk_while_encoding_is_a_transient_failure_with_cleanup(env: Env) -> None:
    source = clip(env)
    disk = FakeDisk()

    def fill_up(call: int) -> None:
        if call == 2:
            disk.free = 1024  # the disk filled while encoding
            raise ProcessFailedError("ffmpeg", 1, f"[Errno {errno.ENOSPC}] No space left on device")

    template = cfr_with_audio(env.media_dir / "t.mp4", seconds=1)
    encoder = CopyEncoder(template, fill_up)
    probe = ScriptedProbe(
        FACTS,
        lambda p, d: perfect_output(
            encoder.by_destination[p].spec, encoder.by_destination[p].facts
        ),
    )
    use_case = env.build(encoder, probe, disk=disk)
    first = env.ok(use_case, source, max_long_edge=480)

    with pytest.raises(ProcessFailedError):
        env.ensure(use_case, source, max_long_edge=320)

    rows = db_rows(
        env, "SELECT state, failure, error_message FROM attempts ORDER BY seq DESC LIMIT 1"
    )
    assert rows[0][:2] == ("failed", "transient") and "free disk space is low" in str(rows[0][2])
    served = env.catalog.find(source, "analysis")
    assert served is not None and served.proxy_asset_id == first.proxy_asset_id
    assert scratch_dirs(env) == []


def test_requests_that_cannot_be_served_fail_with_a_reason(env: Env) -> None:
    use_case, _, _ = env.scripted(FACTS)
    sound = env.import_media(audio_only(env.media_dir / "sound.m4a"))

    not_video = env.ensure(use_case, sound)
    missing = env.ensure(use_case, "does-not-exist")
    bad_profile = env.ensure(use_case, clip(env), "huge")

    assert isinstance(not_video, Err) and isinstance(not_video.error, ValidationError)
    assert isinstance(missing, Err) and isinstance(missing.error, NotFoundError)
    assert isinstance(bad_profile, Err) and "unknown profile" in str(bad_profile.error)


# --------------------------------------------------------------------- concurrency and fencing
def test_an_older_request_finishing_after_a_newer_one_does_not_replace_it(env: Env) -> None:
    source = clip(env)
    gate = Gate(1)
    use_case, encoder, _ = env.scripted(FACTS, on_encode=gate)
    older, box = in_thread(lambda: env.ensure(use_case, source, max_long_edge=480))
    assert gate.inside.wait(30)

    newer = env.ok(use_case, source, max_long_edge=320)  # submitted later, finishes first
    gate.release.set()
    older.join(30)

    result = box["result"]
    assert isinstance(result, Err) and isinstance(result.error, Superseded)
    served = env.catalog.find(source, "analysis")
    assert served is not None and served.proxy_asset_id == newer.proxy_asset_id
    assert attempts(env) == [("superseded", None), ("ready", None)] or sorted(attempts(env)) == [
        ("ready", None), ("superseded", None),
    ]  # fmt: skip
    # the older result was kept: asking for it later activates it without encoding
    again = env.ok(use_case, source, max_long_edge=480)
    assert again.reused and len(encoder.calls) == 2


def test_requests_that_finish_in_submission_order_end_with_the_newest_served(env: Env) -> None:
    source = clip(env)
    use_case, _, _ = env.scripted(FACTS)

    env.ok(use_case, source, max_long_edge=480)
    newest = env.ok(use_case, source, max_long_edge=320)

    served = env.catalog.find(source, "analysis")
    assert served is not None and served.proxy_asset_id == newest.proxy_asset_id


def test_a_newer_request_that_fails_does_not_stop_the_older_one(env: Env) -> None:
    source = clip(env)
    gate = Gate(1)

    def maybe_fail(call: int) -> None:
        gate(call)
        if call == 2:
            raise ProcessFailedError("ffmpeg", 1, "newer request failed")

    use_case, _, _ = env.scripted(FACTS, on_encode=maybe_fail)
    older, box = in_thread(lambda: env.ensure(use_case, source, max_long_edge=480))
    assert gate.inside.wait(30)

    with pytest.raises(ProcessFailedError):
        env.ensure(use_case, source, max_long_edge=320)
    gate.release.set()
    older.join(30)

    result = box["result"]
    assert isinstance(result, Ok)
    served = env.catalog.find(source, "analysis")
    assert served is not None and served.proxy_asset_id == result.value.proxy_asset_id


# ----------------------------------------------------- fingerprint ownership, repair, recovery
def spec_and_fingerprint(
    env: Env, source: str, **overrides: JsonValue
) -> tuple[ResolvedSpec, dict[str, JsonValue], str]:
    spec = resolve_spec(resolve_profile("analysis", overrides), FACTS)
    config = spec.to_config()
    fingerprint = env.library.fingerprint(source, PROXY_OPERATION, config, SPEC_VERSION)
    assert isinstance(fingerprint, Ok)
    return spec, config, fingerprint.value


def register_directly(env: Env, source: str, config: dict[str, JsonValue]) -> str:
    registered = env.library.register_derived(
        source,
        cfr_with_audio(env.media_dir / "stray.mp4", seconds=1),
        operation=PROXY_OPERATION,
        config=config,
        version=SPEC_VERSION,
        role=AssetRole.PREVIEW,
    )
    assert isinstance(registered, Ok)
    return registered.value.asset.id


def test_a_proxy_registered_by_a_crashed_attempt_is_verified_then_adopted_without_encoding(
    env: Env,
) -> None:
    source = clip(env)
    spec, config, fingerprint = spec_and_fingerprint(env, source)
    stray = register_directly(env, source, config)  # the crash: registered, never committed
    SqliteProxyStore(env.paths.data_dir / "proxy_manager.db", owner_token="dead").begin_attempt(
        Attempt(
            id="crashed", source_asset_id=source, profile="analysis", profile_version=1,
            spec_fingerprint=fingerprint, owner_token="dead", created_at=NOW,
        ),
        free_bytes=10**12, margin_bytes=0, now=NOW, lease_seconds=120.0,
    )  # fmt: skip
    encoder = CopyEncoder(env.media_dir / "unused.mp4")
    probe = ScriptedProbe(FACTS, lambda p, d: replace(perfect_output(spec, FACTS), decode_errors=0))

    dto = env.ok(env.build(encoder, probe), source)

    assert dto.proxy_asset_id == stray and dto.reused and dto.evidence is Evidence.DECODED
    assert encoder.calls == []
    assert probe.output_probes[0][1] is True  # the stored file was decoded to the end first
    assert env.catalog.condition(source, "analysis") is Condition.READY
    assert attempts(env) == [("interrupted", "transient"), ("ready", None)]


def test_a_stray_proxy_that_fails_verification_is_replaced_because_ownership_is_proven(
    env: Env,
) -> None:
    source = clip(env)
    spec, config, fingerprint = spec_and_fingerprint(env, source)
    stray = register_directly(env, source, config)
    store = SqliteProxyStore(env.paths.data_dir / "proxy_manager.db", owner_token="dead")
    store.begin_attempt(
        Attempt(id="crashed", source_asset_id=source, profile="analysis", profile_version=1,
                spec_fingerprint=fingerprint, owner_token="dead", created_at=NOW),
        free_bytes=10**12, margin_bytes=0, now=NOW, lease_seconds=120.0,
    )  # fmt: skip
    template = cfr_with_audio(env.media_dir / "t.mp4", seconds=1)
    encoder = CopyEncoder(template)

    def output(path: Path, decode: bool) -> OutputFacts:
        if path in encoder.by_destination:
            request = encoder.by_destination[path]
            return replace(
                perfect_output(request.spec, request.facts), decode_errors=0 if decode else None
            )
        broken = perfect_output(spec, FACTS)
        assert broken.video is not None
        return replace(broken, video=replace(broken.video, frames=1), decode_errors=0)

    dto = env.ok(env.build(encoder, ScriptedProbe(FACTS, output)), source)

    assert dto.proxy_asset_id != stray and not dto.reused and len(encoder.calls) == 1
    assert isinstance(env.library.get(stray), Err)  # the unusable one was released
    assert env.catalog.condition(source, "analysis") is Condition.READY


def test_a_missing_proxy_file_is_detected_and_repaired_with_a_fresh_asset(env: Env) -> None:
    source = clip(env)
    use_case, encoder, _ = env.scripted(FACTS)
    first = env.ok(use_case, source)
    path = env.library.local_path(first.proxy_asset_id)
    assert isinstance(path, Ok)
    path.value.unlink()  # the file vanishes from library storage
    assert env.catalog.condition(source, "analysis") is Condition.MISSING

    repaired = env.ok(use_case, source)

    assert not repaired.reused and repaired.proxy_asset_id != first.proxy_asset_id
    assert len(encoder.calls) == 2
    assert env.catalog.condition(source, "analysis") is Condition.READY
    assert isinstance(env.library.get(first.proxy_asset_id), Err)  # the broken asset is gone
    assert env.library.get(source) is not None


def test_a_broken_proxy_with_derived_assets_is_not_deleted_out_from_under_them(env: Env) -> None:
    source = clip(env)
    use_case, encoder, _ = env.scripted(FACTS)
    first = env.ok(use_case, source)
    thumbnail = env.library.ensure_thumbnail(first.proxy_asset_id)  # something derives from it
    assert isinstance(thumbnail, Ok)
    path = env.library.local_path(first.proxy_asset_id)
    assert isinstance(path, Ok)
    path.value.unlink()

    result = env.ensure(use_case, source)

    assert isinstance(result, Err) and isinstance(result.error, ArtifactUnrepairable)
    assert isinstance(env.library.get(first.proxy_asset_id), Ok)
    assert isinstance(env.library.get(thumbnail.value.id), Ok)
    assert attempts(env)[-1] == ("failed", "permanent") and len(encoder.calls) == 2


def test_an_asset_this_module_did_not_create_is_never_replaced_or_deleted(env: Env) -> None:
    source = clip(env)
    _, config, _ = spec_and_fingerprint(env, source)
    foreign = register_directly(env, source, config)  # no attempt ever recorded for it
    use_case, encoder, _ = env.scripted(FACTS)

    result = env.ensure(use_case, source)

    assert isinstance(result, Err) and isinstance(result.error, ArtifactUnrepairable)
    assert "not created by this module" in str(result.error)
    assert isinstance(env.library.get(foreign), Ok) and encoder.calls == [] and attempts(env) == []


def test_an_archived_proxy_is_reported_not_overwritten(env: Env) -> None:
    source = clip(env)
    use_case, encoder, _ = env.scripted(FACTS)
    first = env.ok(use_case, source)
    clock = env.container.resolve(Clock)
    assert isinstance(clock, FixedClock)
    clock.advance(seconds=1)  # archiving is a no-op if time stood still
    assert isinstance(env.library.archive(first.proxy_asset_id), Ok)
    # make the slot ask again: nothing is "served" while the asset is archived
    result = env.ensure(use_case, source)

    assert isinstance(result, Err) and isinstance(result.error, ArtifactUnrepairable)
    assert len(encoder.calls) == 1 and isinstance(env.library.get(first.proxy_asset_id), Ok)


def test_a_verdict_from_an_older_validator_is_rechecked_against_the_stored_file(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = clip(env)
    use_case, encoder, probe = env.scripted(FACTS)
    first = env.ok(use_case, source)
    # the validation policy moves on: the stored verdict (made by version 1) is now outdated
    monkeypatch.setattr(
        "media_house.modules.proxy_manager.application.ensure_proxy.VALIDATOR_VERSION", 2
    )
    monkeypatch.setattr("media_house.modules.proxy_manager.application.lookup.VALIDATOR_VERSION", 2)
    assert env.catalog.condition(source, "analysis") is Condition.NEEDS_REVALIDATION
    probes_before = len(probe.output_probes)

    again = env.ok(use_case, source)

    assert again.reused and len(encoder.calls) == 1
    assert probe.output_probes[probes_before:][0][1] is True  # re-checked, decoded to the end
    record = env.store.get_artifact(first.proxy_asset_id)
    assert record is not None and record.evidence is Evidence.DECODED


def test_stored_facts_from_media_inspection_are_used_and_the_source_is_not_probed(env: Env) -> None:
    source = clip(env)
    env.inspect(source)
    use_case, encoder, probe = env.scripted(FACTS, with_inspections=True)

    dto = env.ok(use_case, source)

    assert probe.source_probes == 0 and dto.reused is False
    assert encoder.calls[0].facts.origin == "inspection"
    asset = env.library.get(dto.proxy_asset_id)
    assert isinstance(asset, Ok)
    provenance = read_proxy_provenance(asset.value.metadata)
    assert provenance is not None and provenance.facts_origin == "inspection"


def test_without_a_stored_inspection_the_module_still_works_by_probing(env: Env) -> None:
    source = clip(env)
    use_case, _, probe = env.scripted(FACTS, with_inspections=True)

    env.ok(use_case, source)

    assert probe.source_probes == 1


# --------------------------------------------------------------------------------- recovery
def test_recovery_retires_dead_attempts_and_sweeps_only_directories_they_own(env: Env) -> None:
    source = clip(env)
    root = env.paths.temp_dir
    for name in ("proxy-dead", "proxy-unknown", "somebody-elses"):
        (root / name).mkdir(parents=True)
        (root / name / "file.bin").write_bytes(b"x")
    SqliteProxyStore(env.paths.data_dir / "proxy_manager.db", owner_token="dead").begin_attempt(
        Attempt(id="dead", source_asset_id=source, profile="analysis", profile_version=1,
                spec_fingerprint="9" * 64, owner_token="dead", created_at=NOW,
                scratch_name="proxy-dead"),
        free_bytes=10**12, margin_bytes=0, now=NOW, lease_seconds=120.0,
    )  # fmt: skip
    use_case, _, _ = env.scripted(FACTS)

    use_case.recover()
    use_case.recover()  # idempotent

    assert attempts(env) == [("interrupted", "transient")]
    assert sorted(p.name for p in root.iterdir() if p.is_dir()) == [
        "proxy-unknown",
        "somebody-elses",
    ]
    assert db_rows(env, "SELECT scratch_name FROM attempts") == [(None,)]


def test_a_dead_attempt_no_longer_blocks_the_same_request(env: Env) -> None:
    source = clip(env)
    _, _, fingerprint = spec_and_fingerprint(env, source)
    SqliteProxyStore(env.paths.data_dir / "proxy_manager.db", owner_token="dead").begin_attempt(
        Attempt(id="dead", source_asset_id=source, profile="analysis", profile_version=1,
                spec_fingerprint=fingerprint, owner_token="dead", created_at=NOW),
        free_bytes=10**12, margin_bytes=0, now=NOW, lease_seconds=120.0,
    )  # fmt: skip
    use_case, encoder, _ = env.scripted(FACTS)

    dto = env.ok(use_case, source)  # not AttemptInFlight

    assert not dto.reused and len(encoder.calls) == 1
    assert FailureKind.TRANSIENT.value in {str(a[1]) for a in attempts(env)}
    assert AttemptState.READY.value in {str(a[0]) for a in attempts(env)}


# ------------------------------------------------- the library hands back someone else's file
def test_a_proxy_registered_by_someone_else_during_the_encode_is_judged_not_assumed(
    env: Env,
) -> None:
    source = clip(env)
    _, config, _ = spec_and_fingerprint(env, source)
    rival: list[str] = []

    def register_meanwhile(call: int) -> None:
        # between our reuse check and our registration another writer registers the derivation
        rival.append(register_directly(env, source, config))

    use_case, encoder, probe = env.scripted(FACTS, on_encode=register_meanwhile)

    dto = env.ok(use_case, source)

    assert dto.proxy_asset_id == rival[0] and len(encoder.calls) == 1
    assert dto.evidence is Evidence.DECODED  # the file the library serves was decoded first
    assert any(decode for _, decode in probe.output_probes[1:])


def test_the_library_serving_a_broken_rival_is_refused_not_activated(env: Env) -> None:
    source = clip(env)
    _, config, _ = spec_and_fingerprint(env, source)
    template = cfr_with_audio(env.media_dir / "t.mp4", seconds=1)
    rival: list[str] = []
    encoder = CopyEncoder(
        template, lambda call: rival.append(register_directly(env, source, config))
    )

    def output(path: Path, decode: bool) -> OutputFacts:
        request = encoder.by_destination.get(path)
        if request is not None:
            return perfect_output(request.spec, request.facts)
        good = perfect_output(encoder.calls[-1].spec, encoder.calls[-1].facts)
        assert good.video is not None
        return replace(good, video=replace(good.video, frames=3), decode_errors=0)

    result = env.ensure(env.build(encoder, ScriptedProbe(FACTS, output)), source)

    assert isinstance(result, Err) and isinstance(result.error, ArtifactUnrepairable)
    assert env.catalog.find(source, "analysis") is None  # nothing was activated
    assert attempts(env)[-1] == ("failed", "permanent")


# ----------------------------------------------------- failures while saving and cancelling late
class RegistrationFails:
    """The real library, except that registering a derived asset fails like a full disk."""

    def __init__(self, real: MediaLibrary, error: Exception) -> None:
        self._real = real
        self._error = error
        self.armed = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    def register_derived(self, *args: object, **kwargs: object) -> object:
        if self.armed:
            raise self._error
        return self._real.register_derived(*args, **kwargs)  # type: ignore[arg-type]


def test_a_full_disk_while_registering_keeps_the_previous_proxy_and_cleans_up(env: Env) -> None:
    source = clip(env)
    template = cfr_with_audio(env.media_dir / "t.mp4", seconds=1)
    encoder = CopyEncoder(template)
    probe = ScriptedProbe(
        FACTS,
        lambda p, d: perfect_output(
            encoder.by_destination[p].spec, encoder.by_destination[p].facts
        ),
    )
    library = RegistrationFails(env.library, InfrastructureError("No space left on device"))
    use_case = EnsureProxy(
        cast(MediaLibrary, library), None, probe, encoder, env.store, FakeDisk(),
        env.container.resolve(Scratch), env.container.resolve(Clock),
    )  # fmt: skip
    first = env.ok(use_case, source, max_long_edge=480)
    library.armed = True

    with pytest.raises(InfrastructureError):
        env.ensure(use_case, source, max_long_edge=320)

    served = env.catalog.find(source, "analysis")
    assert served is not None and served.proxy_asset_id == first.proxy_asset_id
    assert attempts(env)[-1] == ("failed", "transient") and scratch_dirs(env) == []
    assert len(env.library.list_derived(source)) == 1


class NullProgress:
    def report(self, current: int, total: int | None = None, message: str = "") -> None:
        return None


def test_cancelling_while_the_proxy_is_being_verified_publishes_nothing(env: Env) -> None:
    source = clip(env)
    token = CancellationToken()
    template = cfr_with_audio(env.media_dir / "t.mp4", seconds=1)
    encoder = CopyEncoder(template)

    def cancelling_probe(path: Path, decode: bool) -> OutputFacts:
        token.cancel()  # the user cancels while the finished file is being checked
        return perfect_output(encoder.by_destination[path].spec, encoder.by_destination[path].facts)

    use_case = env.build(encoder, ScriptedProbe(FACTS, cancelling_probe))
    context = JobContext("cancel-me", token, NullProgress())

    with pytest.raises(OperationCancelledError):
        use_case.execute(EnsureProxyCommand(source, "analysis"), context)

    assert attempts(env) == [("cancelled", "cancelled")]
    assert env.library.list_derived(source) == [] and scratch_dirs(env) == []
    assert env.catalog.find(source, "analysis") is None
