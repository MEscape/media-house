"""The use case: stored once, found again, never touching the original."""

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from media_house.modules.media_inspection.application.inspect_media import InspectMediaCommand
from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.values import Depth
from media_house.modules.media_library.application.contracts import MediaType
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, OperationCancelledError, ValidationError
from tests.integration.media_inspection.conftest import Env

pytestmark = pytest.mark.integration


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestStoring:
    def test_the_inspection_is_a_derived_document_of_the_source(self, env: Env) -> None:
        source_id = env.import_media(env.samples.cfr())

        result = env.inspect(source_id)

        assert result.created
        asset = result.asset
        assert asset.media_type is MediaType.OTHER
        assert asset.derivation is not None
        assert asset.derivation.source_asset_id == source_id
        assert asset.derivation.operation == "media_inspection"
        assert asset.metadata["verdict"] == result.inspection.status.verdict.value
        assert asset.metadata["usable"] is True
        assert asset.technical["document_type"] == "media_inspection"
        assert result.inspection.asset_id == source_id

    def test_the_original_is_never_modified(self, env: Env) -> None:
        path = env.samples.cfr()
        before = digest(path)
        source_id = env.import_media(path)

        env.inspect(source_id)

        stored = env.library.local_path(source_id)
        assert isinstance(stored, Ok)
        assert digest(stored.value) == before
        assert digest(path) == before

    def test_only_audio_and_video_can_be_inspected(self, env: Env, tmp_path: Path) -> None:
        picture = tmp_path / "picture.png"
        Image.new("RGB", (8, 8), "red").save(picture)
        image_id = env.import_media(picture)

        result = env.use_case.execute(InspectMediaCommand(image_id), JobContext.detached())

        assert isinstance(result, Err)
        assert isinstance(result.error, ValidationError)

    def test_an_unknown_asset_is_an_error_result(self, env: Env) -> None:
        result = env.use_case.execute(InspectMediaCommand("missing"), JobContext.detached())

        assert isinstance(result, Err)


class TestReuse:
    def test_the_same_request_is_answered_from_the_library_without_probing(self, env: Env) -> None:
        source_id = env.import_media(env.samples.cfr())
        first = env.inspect(source_id)
        env.runner.reset()

        second = env.inspect(source_id)

        assert not second.created
        assert second.asset.id == first.asset.id
        assert second.inspection == first.inspection
        # only the cheap version lookups that identify the cache entry: no probing, no decoding
        assert env.runner.count("ffprobe", "-show_streams") == 0
        assert env.runner.count("ffprobe", "-show_frames") == 0
        assert env.runner.count("ffmpeg", "-f", "null") == 0

    def test_a_restarted_application_finds_the_stored_inspection(self, env: Env) -> None:
        source_id = env.import_media(env.samples.cfr())
        first = env.inspect(source_id)
        env.runner.reset()

        again = env.inspect(source_id, use_case=env.build())

        assert not again.created
        assert again.asset.id == first.asset.id
        assert env.runner.count("ffprobe", "-show_streams") == 0

    def test_a_full_inspection_also_answers_a_probe_request(self, env: Env) -> None:
        source_id = env.import_media(env.samples.cfr())
        full = env.inspect(source_id, InspectionConfig(depth=Depth.FULL))

        cheap = env.inspect(source_id, InspectionConfig(depth=Depth.PROBE))

        assert not cheap.created
        assert cheap.asset.id == full.asset.id

    def test_a_probe_inspection_does_not_answer_a_full_request(self, env: Env) -> None:
        source_id = env.import_media(env.samples.cfr())
        cheap = env.inspect(source_id, InspectionConfig(depth=Depth.PROBE))

        full = env.inspect(source_id, InspectionConfig(depth=Depth.FULL))

        assert full.created
        assert full.asset.id != cheap.asset.id
        assert full.inspection.observed.integrity.decode_checked

    def test_a_different_threshold_is_a_different_inspection(self, env: Env) -> None:
        source_id = env.import_media(env.samples.late_audio())
        default = env.inspect(source_id)

        strict = env.inspect(source_id, InspectionConfig(sync_tolerance_seconds=0.001))

        assert strict.created
        assert strict.asset.id != default.asset.id

    def test_identical_media_imported_twice_is_inspected_once(
        self, env: Env, tmp_path: Path
    ) -> None:
        copy = tmp_path / "copy.mp4"
        copy.write_bytes(env.samples.cfr().read_bytes())

        first = env.inspect(env.import_media(env.samples.cfr()))
        second = env.inspect(env.import_media(copy))

        assert not second.created
        assert second.asset.id == first.asset.id

    def test_a_damaged_stored_document_is_replaced_and_then_reused(self, env: Env) -> None:
        source_id = env.import_media(env.samples.cfr())
        first = env.inspect(source_id)
        stored = env.library.local_path(first.asset.id)
        assert isinstance(stored, Ok)
        stored.value.write_text("{ this is not an inspection", encoding="utf-8")

        healed = env.inspect(source_id)
        reused = env.inspect(source_id)

        assert healed.created
        assert healed.inspection.primary_video == first.inspection.primary_video
        assert not reused.created
        assert reused.asset.id == healed.asset.id


class TestFind:
    def test_find_returns_what_exists_and_never_probes(self, env: Env) -> None:
        source_id = env.import_media(env.samples.cfr())
        assert env.use_case.find(source_id) is None
        env.inspect(source_id)
        env.runner.reset()

        found = env.use_case.find(source_id)

        assert found is not None
        assert found.inspection.asset_id == source_id
        assert env.runner.count("ffprobe", "-show_streams") == 0

    def test_find_with_a_cheaper_config_finds_the_full_inspection(self, env: Env) -> None:
        source_id = env.import_media(env.samples.cfr())
        env.inspect(source_id)

        assert env.use_case.find(source_id, InspectionConfig(depth=Depth.PROBE)) is not None

    def test_find_for_an_unknown_asset_is_none_not_an_error(self, env: Env) -> None:
        assert env.use_case.find("no-such-asset") is None


class TestCancellation:
    def test_a_cancelled_job_stops_before_probing(self, env: Env) -> None:
        source_id = env.import_media(env.samples.cfr())
        ctx = JobContext.detached()
        ctx.cancellation.cancel()
        env.runner.reset()

        with pytest.raises(OperationCancelledError):
            env.use_case.execute(InspectMediaCommand(source_id), ctx)

        assert env.runner.count("ffprobe", "-show_streams") == 0
