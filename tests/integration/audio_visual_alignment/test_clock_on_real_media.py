"""The video origin, proven on real files in two containers.

FFmpeg reports the first frame relative to the container start for MP4 but not always for
MPEG-TS, so the common clock must be derived from the inspection. Each case compares the
corrected time of the first frame with what ffprobe says independently.
"""

import pytest

from media_house.modules.audio_visual_alignment.application.align import AlignCommand
from media_house.modules.audio_visual_alignment.domain.values import CorrectionSource, HowObtained
from media_house.modules.video_intelligence.application.contracts import VideoAnalysis
from tests.integration.audio_visual_alignment.conftest import (
    AlignEnv,
    ffmpeg,
    make_offset_clip,
    probe_first_frame_seconds,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("suffix", [".mp4", ".mov", ".mkv"])
def test_the_first_frame_lands_where_ffprobe_says_on_the_container_timeline(
    aenv: AlignEnv, suffix: str
) -> None:
    clip = make_offset_clip(aenv.root / f"offset{suffix}")
    asset_id = aenv.import_clip(clip)
    aenv.analyze_video(asset_id)
    frame, container_start = probe_first_frame_seconds(clip)
    truth = frame - container_start

    result = aenv.align(
        AlignCommand(asset_id, audio_timeline=aenv.audio_timeline(asset_id), use_content=False)
    )

    doc = result.document
    stored = aenv.video_analyzer
    assert stored.calls == 0  # the stored analysis was reused, not recomputed
    analysis = _stored_analysis(aenv, asset_id)
    analysed_first = analysis.shots[0].range.start.seconds
    corrected = analysed_first + doc.video_correction
    assert corrected == pytest.approx(truth, abs=0.002), (
        f"{suffix}: analysis says {analysed_first}, ffprobe says {truth}"
    )
    video_fix = next(c for c in doc.sync.corrections if c.stream == "video")
    assert video_fix.source is CorrectionSource.INSPECTION
    assert {r.name: r.how for r in doc.inputs}["inspection"] is HowObtained.REUSED


def test_a_clip_without_offsets_needs_no_correction(aenv: AlignEnv) -> None:
    clip = aenv.root / "plain.mp4"
    ffmpeg(
        *["-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2"],
        *["-f", "lavfi", "-i", "sine=frequency=440:duration=2"],
        *["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)],
    )
    asset_id = aenv.import_clip(clip)
    aenv.analyze_video(asset_id)

    doc = aenv.align(
        AlignCommand(asset_id, audio_timeline=aenv.audio_timeline(asset_id), use_content=False)
    ).document

    assert abs(doc.video_correction) < 0.05


def _stored_analysis(aenv: AlignEnv, asset_id: str) -> VideoAnalysis:
    from media_house.modules.video_intelligence.application.results import VideoResults
    from media_house.shared.errors import Ok

    found = VideoResults(aenv.library).latest(asset_id)
    assert isinstance(found, Ok) and found.value is not None
    return found.value[0]
