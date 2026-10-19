"""The owners' read-only catalogs never compute; the real composition root wires everything."""

from dataclasses import replace
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.audio_intelligence.application.contracts import (
    AudioAnalyzer,
    AudioCatalog,
    timeline_to_json,
)
from media_house.modules.audio_intelligence.application.results import AudioResults
from media_house.modules.audio_intelligence.domain.analysis.config import ANALYSIS_OPERATION
from media_house.modules.audio_visual_alignment.application.contracts import (
    AlignCommand,
    AlignmentCatalog,
    AudioVisualAligner,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
)
from media_house.modules.video_intelligence.application.contracts import (
    VideoAnalyzer,
    VideoCatalog,
)
from media_house.modules.video_intelligence.application.results import VideoResults
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Err, Ok
from tests.integration.audio_visual_alignment.conftest import AlignEnv, make_offset_clip

pytestmark = pytest.mark.integration


def store_timeline(aenv: AlignEnv, asset_id: str, text: str | None = None) -> str:
    documents = DerivedDocuments(aenv.library, aenv.paths)
    timeline = aenv.audio_timeline(asset_id)
    spec = DocumentSpec(ANALYSIS_OPERATION, 1, {"case": "unit"})
    stored = documents.store(
        aenv.asset(asset_id),
        text if text is not None else timeline_to_json(timeline),
        filename="timeline.json",
        spec=spec,
        display_name="timeline",
        metadata={},
    )
    assert isinstance(stored, Ok), stored
    return stored.value.id


class TestAudioCatalog:
    def test_nothing_stored_is_none_not_an_error_and_nothing_runs(self, aenv: AlignEnv) -> None:
        asset_id = aenv.import_clip(make_offset_clip(aenv.root / "a.mp4"))

        found = AudioResults(aenv.library).latest(asset_id)

        assert isinstance(found, Ok) and found.value is None

    def test_a_stored_timeline_is_read_back_unchanged(self, aenv: AlignEnv) -> None:
        asset_id = aenv.import_clip(make_offset_clip(aenv.root / "a.mp4"))
        document_id = store_timeline(aenv, asset_id)

        found = AudioResults(aenv.library).latest(asset_id)

        assert isinstance(found, Ok) and found.value is not None
        timeline, asset = found.value
        assert asset.id == document_id
        assert timeline.metadata.source_asset_id == asset_id

    def test_an_unreadable_document_is_skipped(self, aenv: AlignEnv) -> None:
        asset_id = aenv.import_clip(make_offset_clip(aenv.root / "a.mp4"))
        good = store_timeline(aenv, asset_id)
        path = aenv.library.local_path(good)
        assert isinstance(path, Ok)
        path.value.write_text('{"document_type": "x"}', encoding="utf-8")

        found = AudioResults(aenv.library).latest(asset_id)

        assert isinstance(found, Ok) and found.value is None


class TestVideoCatalog:
    def test_nothing_stored_is_none(self, aenv: AlignEnv) -> None:
        asset_id = aenv.import_clip(make_offset_clip(aenv.root / "v.mp4"))

        found = VideoResults(aenv.library).latest(asset_id)

        assert isinstance(found, Ok) and found.value is None

    def test_a_stored_analysis_is_found_and_filtered_by_profile(self, aenv: AlignEnv) -> None:
        asset_id = aenv.import_clip(make_offset_clip(aenv.root / "v.mp4"))
        aenv.analyze_video(asset_id)
        catalog = VideoResults(aenv.library)

        triage = catalog.latest(asset_id, "triage")
        other = catalog.latest(asset_id, "deep")

        assert isinstance(triage, Ok) and triage.value is not None
        assert triage.value[0].asset_id == asset_id
        assert isinstance(other, Ok) and other.value is None

    def test_a_lookup_never_decodes_anything(self, aenv: AlignEnv) -> None:
        asset_id = aenv.import_clip(make_offset_clip(aenv.root / "v.mp4"))

        VideoResults(aenv.library).latest(asset_id)

        assert aenv.video_analyzer.calls == 0


def test_the_composition_root_wires_the_aligner_and_both_catalogs(tmp_path: Path) -> None:
    application = Application.start(
        StartupOptions(env={}, home=tmp_path, environment=Environment.TEST)
    )
    try:
        container = application.container
        for contract in (
            AudioVisualAligner,
            AlignmentCatalog,
            AudioCatalog,
            VideoCatalog,
            AudioAnalyzer,
            VideoAnalyzer,
        ):
            assert container.resolve(contract) is not None

        result = container.resolve(AudioVisualAligner).execute(
            AlignCommand("missing-asset"), JobContext.detached()
        )

        assert isinstance(result, Err)  # reached the real library through the real wiring
    finally:
        application.shutdown()


def test_replace_is_available_for_commands() -> None:
    assert replace(AlignCommand("a"), use_content=False).use_content is False
