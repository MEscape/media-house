"""MPEG-TS: the video origin checked against an independent ffprobe reference.

The Media Library cannot import ``.ts`` today, so this goes around it: the real FFmpeg frame
decoder and the real ffprobe prober run on the file directly, and the alignment clock is built
from exactly what they report. The reference is ffprobe's own first presented frame minus the
container start, read by a different code path than either module.
"""

import shutil
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from media_house.core.domain import Rational
from media_house.core.infrastructure.process import SubprocessRunner
from media_house.modules.audio_visual_alignment.application.views import clock_facts
from media_house.modules.audio_visual_alignment.domain.clock import Clock, build_corrections
from media_house.modules.audio_visual_alignment.domain.config import AlignmentProfile
from media_house.modules.audio_visual_alignment.domain.engine import align
from media_house.modules.audio_visual_alignment.domain.inputs import Inputs
from media_house.modules.audio_visual_alignment.domain.values import CorrectionSource, FlagKind
from media_house.modules.media_inspection.application.contracts import MediaInspection
from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.infrastructure.ffprobe_prober import FfprobeProber
from media_house.modules.video_intelligence.application.ports import DecodeRequest
from media_house.modules.video_intelligence.domain.profiles import get_profile
from media_house.modules.video_intelligence.domain.source import SourceInfo
from media_house.modules.video_intelligence.infrastructure.ffmpeg_frames import FfmpegFrameSource
from media_house.shared.concurrency import CancellationToken
from tests.integration.audio_visual_alignment.conftest import (
    ffmpeg,
    probe_first_frame_seconds,
)
from tests.support.avl_facts import audio, segment, shot, video
from tests.unit.audio_visual_alignment.helpers import NOW

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="FFmpeg and ffprobe are required",
    ),
]

SOURCE = SourceInfo(
    width=160,
    height=120,
    frame_rate=Rational(25, 1),
    duration_seconds=2.0,
    declared_frame_count=None,
    rotation=0,
    variable_frame_rate=False,
    color_transfer=None,
    color_range=None,
)


def make_clip(path: Path, *, video_start: float, audio_start: float) -> Path:
    ffmpeg(
        *[
            "-itsoffset",
            str(video_start),
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=25:duration=2",
        ],
        *["-itsoffset", str(audio_start), "-f", "lavfi", "-i", "sine=frequency=440:duration=5"],
        *["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"],
        str(path),
    )
    return path


def decoded_first_frame(path: Path, work: Path) -> float:
    request = DecodeRequest(
        source=SOURCE,
        settings=get_profile("triage").measurement,
        stride=1,
        rgb_stride=1,
        dense=True,
        samples=False,
        rgb=False,
    )
    decoded = FfmpegFrameSource(SubprocessRunner()).decode(path, request, work, CancellationToken())
    timeline = decoded.timeline
    return timeline.pts[0] * timeline.timebase.value


def inspected(path: Path) -> MediaInspection:
    observed = FfprobeProber(SubprocessRunner()).probe(
        path, InspectionConfig(), CancellationToken()
    )
    # clock_facts reads only .container, .video_streams and .audio_streams, which ObservedMedia has
    return cast(MediaInspection, observed)


@pytest.mark.parametrize(
    ("suffix", "video_start", "audio_start"),
    [(".ts", 1.0, 0.5), (".ts", 0.0, 0.3), (".ts", 2.0, 0.0), (".mp4", 1.0, 0.5)],
)
def test_the_corrected_first_frame_matches_the_independent_reference(
    tmp_path: Path, suffix: str, video_start: float, audio_start: float
) -> None:
    clip = make_clip(tmp_path / f"clip{suffix}", video_start=video_start, audio_start=audio_start)
    frame, container_start = probe_first_frame_seconds(clip)
    truth = frame - container_start
    observed = decoded_first_frame(clip, tmp_path)

    facts = clock_facts(inspected(clip))
    corrections = build_corrections(
        facts, video([shot("s", 0, 2)], first_frame=observed), None, AlignmentProfile()
    )
    fix = next(c for c in corrections if c.stream == "video")
    clock = Clock.of(corrections)

    assert clock.video(observed) == pytest.approx(truth, abs=0.002)
    assert fix.source is CorrectionSource.INSPECTION
    assert fix.observed_seconds == pytest.approx(observed)
    assert fix.expected_seconds == pytest.approx(truth, abs=0.002)
    assert fix.seconds == pytest.approx(truth - observed, abs=0.002)
    assert next(c for c in corrections if c.stream == "audio").seconds == 0.0
    assert clock.audio(1.234) == 1.234  # the audio clock is never altered by this correction


def test_the_correction_is_applied_exactly_once_and_recorded(tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "clip.ts", video_start=1.0, audio_start=0.5)
    observed = decoded_first_frame(clip, tmp_path)
    facts = clock_facts(inspected(clip))
    a = audio([segment("1", 1.0, 2.0)], duration=2.5)
    v = video([shot("s", 0, 2.5)], first_frame=observed, duration=2.5)

    once = align(
        Inputs(audio=a, video=v, clock=facts),
        AlignmentProfile(),
        alignment_id="av_ts",
        created_at=NOW,
        records=(),
        engines={},
    )
    shift = once.video_correction

    # the picture side keeps the analysis' own FrameTime; the audio side is untouched
    assert once.speech_sources[0].shots[0].video_range.start.seconds == 0.0
    assert (once.speech_sources[0].start, once.speech_sources[0].end) == (1.0, 2.0)
    # provenance carries what produced the number
    fix = next(c for c in once.sync.corrections if c.stream == "video")
    assert fix.observed_seconds == pytest.approx(observed)
    assert fix.expected_seconds == pytest.approx(observed + shift)
    flags = {f.kind for f in once.flags}
    assert (FlagKind.VIDEO_ORIGIN_CORRECTED in flags) == (abs(shift) > 0.002)
    assert FlagKind.VIDEO_ORIGIN_UNVERIFIED not in flags

    # feeding the already-corrected time back must not shift it again
    corrected = replace(v, first_frame_seconds=observed + shift)
    again = align(
        Inputs(audio=a, video=corrected, clock=facts),
        AlignmentProfile(),
        alignment_id="av_ts",
        created_at=NOW,
        records=(),
        engines={},
    )
    assert again.video_correction == pytest.approx(0.0, abs=0.002)
