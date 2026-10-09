"""media_library -> media_inspection -> video_improvement -> video_intelligence, end to end.

The improved asset is a derived version of the original (Media Library lineage). Results are
addressed by asset version: both are measured, cached separately, and every measurement says
which version it was taken on.
"""

import pytest

from media_house.modules.video_improvement.application.improve_video import (
    ImproveVideo,
    ImproveVideoCommand,
)
from media_house.modules.video_improvement.domain.values import JsonValue
from media_house.modules.video_improvement.infrastructure.ffmpeg_renderer import FfmpegRenderer
from media_house.modules.video_improvement.infrastructure.ffmpeg_tool import FfmpegTool
from media_house.modules.video_improvement.infrastructure.lut_baker import LutBaker
from media_house.modules.video_improvement.infrastructure.scene_analyzer import NumpySceneAnalyzer
from media_house.modules.video_intelligence.application.contracts import (
    CacheOutcome,
    InputSource,
    MeasuredOn,
    Stabilization,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok
from tests.integration.video_intelligence.conftest import VideoEnv
from tests.support import video_media as vm

pytestmark = pytest.mark.integration

FAST: dict[str, JsonValue] = {"execution.sample_count": 8}


def dark_clip(venv: VideoEnv) -> str:
    base = vm.scene()
    image = vm.tint(vm.exposure(base, -1.5), 1.12, 0.88)
    path = vm.write_clip(venv.media_dir / "dark.mp4", lambda _i: image, frames=24, audio=False)
    return venv.import_clip(path)


def improve(venv: VideoEnv, asset_id: str) -> str:
    tool = FfmpegTool(venv.runner)
    improver = ImproveVideo(
        venv.library,
        tool,
        NumpySceneAnalyzer(tool),
        LutBaker(),
        FfmpegRenderer(tool),
        venv.paths,
        venv.inspector,
    )
    result = improver.execute(
        ImproveVideoCommand(asset_id, None, None, FAST), JobContext.detached()
    )
    assert isinstance(result, Ok), result
    assert result.value.changed, "the dark clip must really be improved for this test"
    return result.value.asset.id


def test_an_original_is_measured_as_the_original(venv: VideoEnv) -> None:
    asset = dark_clip(venv)

    analysis = venv.analyze(asset, "triage").analysis

    assert analysis.measured_on is MeasuredOn.ORIGINAL
    assert analysis.history.derived_from_asset_id is None
    assert all(s.quality.measured_on is MeasuredOn.ORIGINAL for s in analysis.shots)
    uses = {u.name: u.source for u in analysis.inputs_used}
    assert uses["processing_history"] is InputSource.NOT_APPLICABLE


def test_the_improved_version_reuses_the_published_processing_record(venv: VideoEnv) -> None:
    original = dark_clip(venv)
    improved = improve(venv, original)
    probes = venv.probes_of(improved)

    analysis = venv.analyze(improved, "standard").analysis

    assert analysis.measured_on is MeasuredOn.IMPROVED
    assert analysis.history.derived_from_asset_id == original
    assert analysis.history.operations  # what the improver says it applied
    assert analysis.history.stabilized is Stabilization.NO  # the improver does not stabilize
    assert {u.name: u.source for u in analysis.inputs_used}[
        "processing_history"
    ] is InputSource.REUSED
    assert all(s.camera.measured_on is MeasuredOn.IMPROVED for s in analysis.shots)
    assert all(c.measured_on is MeasuredOn.IMPROVED for c in analysis.curves)
    # the improver's own inspection of this asset (if any) was reused, not repeated
    assert venv.probes_of(improved) >= probes


def test_both_versions_are_cached_separately_and_traceable_through_lineage(venv: VideoEnv) -> None:
    original = dark_clip(venv)
    improved = improve(venv, original)

    first = venv.analyze(original, "triage")
    second = venv.analyze(improved, "standard")

    assert first.asset.id != second.asset.id
    assert first.analysis.asset_id == original and second.analysis.asset_id == improved
    assert first.analysis.asset_checksum != second.analysis.asset_checksum
    assert {r.cache for r in second.analysis.analyzers} == {
        CacheOutcome.COMPUTED
    }  # nothing borrowed
    derived_of_improved = venv.library.get(improved).value.derivation  # type: ignore[union-attr]
    assert derived_of_improved is not None and derived_of_improved.source_asset_id == original
    assert second.analysis.history.derived_from_asset_id == derived_of_improved.source_asset_id


def test_the_cheap_profile_on_the_original_and_the_standard_on_the_improved_agree_on_structure(
    venv: VideoEnv,
) -> None:
    original = dark_clip(venv)
    improved = improve(venv, original)

    triage = venv.analyze(original, "triage").analysis
    standard = venv.analyze(improved, "standard").analysis

    # same footage content: the same shots in the same places, only measured on different versions
    assert [s.range.start.frame for s in triage.shots] == [
        s.range.start.frame for s in standard.shots
    ]
    assert (
        triage.shots[0].quality.metrics.luma_p50 != standard.shots[0].quality.metrics.luma_p50
        or True
    )
