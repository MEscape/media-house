"""Reframing Intelligence against the real composition root and the real Media Library.

Real: the container, the library (tiny MP4s through FFmpeg), the video catalog, derived-document
storage, the cache and history. Synthetic: what the clip "shows" (a video analysis with person
tracks stored the way the analyzer stores its own, claiming a 1080p picture). Nothing here says how
good the framing is on real footage; it proves the plumbing, the cache and that nothing is modified.
"""

import hashlib
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.reframing_intelligence.application.contracts import (
    DOCUMENT_TYPE,
    AnalyzeReframingCommand,
    Decision,
    MissingPrerequisite,
    ReframingAnalyzer,
    ReframingCatalog,
    ReframingConfig,
    SafeArea,
    SegmentDecision,
    Severity,
    SourceGeometry,
    TargetFormat,
    crop_problems,
    validate_analysis,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok
from tests.support.cont_library import import_clip, store_analysis, walking_analysis

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="FFmpeg and ffprobe are required to import the test recordings",
    ),
]


def prepare(app: Application, tmp_path: Path) -> tuple[str, ReframingAnalyzer]:
    asset = import_clip(app, tmp_path / "clips", "walk")
    walking = walking_analysis(asset)
    # a 1080p picture (the synthetic default is 320x180)
    walking = replace(walking, source=replace(walking.source, width=1920, height=1080))
    store_analysis(app, asset, walking)
    return asset.id, app.container.resolve(ReframingAnalyzer)


def run(analyzer: ReframingAnalyzer, asset: str, target: str = "9:16", **kw: object):  # type: ignore[no-untyped-def]
    command = AnalyzeReframingCommand((asset,), TargetFormat.of(target), **kw)  # type: ignore[arg-type]
    return analyzer.execute(command, JobContext.detached())


def digest(app: Application, asset: str) -> str:
    path = app.container.resolve(MediaLibrary).local_path(asset)
    assert isinstance(path, Ok)
    return hashlib.sha256(path.value.read_bytes()).hexdigest()


def test_stored_tracks_become_valid_versioned_crops_that_are_stored_and_read_back(
    app: Application, tmp_path: Path
) -> None:
    asset, analyzer = prepare(app, tmp_path)
    before = digest(app, asset)

    result = run(analyzer, asset)

    assert isinstance(result, Ok), result
    analysis = result.value.analysis
    assert result.value.created and analysis.analysis_version == 1 and analysis.supersedes is None
    starts = [r.start.value for r in analysis.recommendations]
    assert len(starts) == 2 and starts == sorted(starts)  # one recommendation per shot, in time
    assert all(r.strategy.has_crop and r.keyframes for r in analysis.recommendations)
    assert [i for i in validate_analysis(analysis) if i.severity is Severity.ERROR] == []
    src = analysis.sources[0]
    geometry = SourceGeometry(
        src.display_width, src.display_height, src.sample_width, src.sample_height, src.rotation,
        src.geometry_exact,
    )  # fmt: skip
    for r in analysis.recommendations:
        for k in r.keyframes:
            assert crop_problems(k.crop, geometry, analysis.target) == []
    # the library holds it as a derived document of the recording, readable through the catalog
    stored = app.container.resolve(ReframingCatalog).latest(asset, target="9:16")
    assert isinstance(stored, Ok) and stored.value is not None
    assert stored.value[0] == analysis and stored.value[1].id == result.value.asset.id
    assert DOCUMENT_TYPE == "reframing_analysis"
    # the original is never modified
    assert digest(app, asset) == before


def test_the_identical_request_is_served_from_the_library_and_a_new_target_is_a_new_analysis(
    app: Application, tmp_path: Path
) -> None:
    asset, analyzer = prepare(app, tmp_path)

    first, again = run(analyzer, asset), run(analyzer, asset)
    square = run(analyzer, asset, "1:1")

    assert isinstance(first, Ok) and isinstance(again, Ok) and isinstance(square, Ok)
    assert first.value.created and not again.value.created  # cache hit: nothing was planned
    assert again.value.asset.id == first.value.asset.id
    assert square.value.created and square.value.asset.id != first.value.asset.id
    catalog = app.container.resolve(ReframingCatalog)
    nine = catalog.latest(asset, target="9:16")
    one = catalog.latest(asset, target="1:1")
    assert isinstance(nine, Ok) and nine.value is not None and nine.value[0].target.name == "9:16"
    assert isinstance(one, Ok) and one.value is not None and one.value[0].target.name == "1:1"
    assert len(catalog.history(asset)) == 2


def test_a_changed_safe_area_makes_a_new_analysis_of_the_same_target_that_names_its_predecessor(
    app: Application, tmp_path: Path
) -> None:
    asset, analyzer = prepare(app, tmp_path)
    first = run(analyzer, asset)
    areas = (SafeArea("captions", 0.05, 0.7, 0.95, 0.98),)

    guarded = run(analyzer, asset, config=ReframingConfig(safe_areas=areas))
    unchanged = run(analyzer, asset)  # the original request: still the first

    assert isinstance(first, Ok) and isinstance(guarded, Ok) and isinstance(unchanged, Ok)
    assert guarded.value.created and guarded.value.analysis.analysis_version == 2
    assert guarded.value.analysis.supersedes == first.value.asset.id
    assert not unchanged.value.created
    assert guarded.value.analysis.safe_areas == areas
    assert first.value.analysis.safe_areas == ()  # earlier analyses are never changed


def test_a_persons_decision_is_kept_through_a_reanalysis(app: Application, tmp_path: Path) -> None:
    asset, analyzer = prepare(app, tmp_path)
    first = run(analyzer, asset)
    assert isinstance(first, Ok)
    key = first.value.analysis.recommendations[0].segment_key

    decided = run(
        analyzer, asset, decisions=(SegmentDecision(key, Decision.REJECTED, "keep wide"),)
    )
    later = run(analyzer, asset, config=ReframingConfig(profile="shorts_reels"))
    back = run(analyzer, asset)

    assert isinstance(decided, Ok) and isinstance(later, Ok) and isinstance(back, Ok)
    rec = decided.value.analysis.recommendations[0]
    assert rec.decision is Decision.REJECTED and rec.decision_note == "keep wide"
    assert (
        rec.strategy == first.value.analysis.recommendations[0].strategy
    )  # the proposal is untouched
    assert back.value.analysis.recommendations[0].decision is Decision.REJECTED  # carried


def test_a_recording_without_a_stored_picture_analysis_cannot_be_framed(
    app: Application, tmp_path: Path
) -> None:
    asset = import_clip(app, tmp_path / "clips", "bare")
    analyzer = app.container.resolve(ReframingAnalyzer)

    result = run(analyzer, asset.id)

    assert isinstance(result, Err) and isinstance(result.error, MissingPrerequisite)


def test_a_supplied_analysis_is_used_as_given_and_recorded(
    app: Application, tmp_path: Path
) -> None:
    asset = import_clip(app, tmp_path / "clips", "walk")
    walking = walking_analysis(asset)
    supplied = replace(walking, source=replace(walking.source, width=1920, height=1080))
    analyzer = app.container.resolve(ReframingAnalyzer)

    result = run(analyzer, asset.id, video_analyses={asset.id: supplied})

    assert isinstance(result, Ok), result
    kinds = {(i.kind, i.how.value) for i in result.value.analysis.inputs}
    assert ("video_analysis", "supplied") in kinds
    assert ("inspection", "not_available") in kinds
    assert ("story_plan", "not_available") in kinds
    assert result.value.analysis.metrics.model_calls == 0
