"""Builders for video improvement domain objects: good footage by default, change what matters."""

from dataclasses import replace

from media_house.modules.video_improvement.domain.color import ColorSpec, Primaries, Transfer
from media_house.modules.video_improvement.domain.measurements import SceneMeasurements
from media_house.modules.video_improvement.domain.source import VideoFacts
from media_house.modules.video_improvement.domain.values import FactsSource, FrameRate

REC709 = ColorSpec(Transfer.BT709, Primaries.BT709)


def measurements(**changes: float | None) -> SceneMeasurements:
    """Well-exposed, neutral, crisp, clean footage that needs nothing."""
    base = SceneMeasurements(
        frames=24,
        luma_p1=0.03,
        luma_p50=0.47,
        luma_p99=0.86,
        linear_p50=0.18,
        linear_p99=0.70,
        highlight_clip=0.0,
        shadow_crush=0.02,
        mean_saturation=0.32,
        cast_red=1.0,
        cast_blue=1.0,
        neutral_share=0.10,
        noise_sigma=0.002,
        sharpness=0.14,
        exposure_variation_stops=0.05,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def facts(**changes: object) -> VideoFacts:
    """A 1080p 25 fps Rec.709 clip with stereo audio, from a probe."""
    base = VideoFacts(
        width=1920,
        height=1080,
        frame_rate=FrameRate(25, 1),
        duration=10.0,
        frame_count=250,
        pixel_format="yuv420p",
        bit_depth=8,
        color=REC709,
        color_range="tv",
        color_matrix="bt709",
        interlaced=False,
        rotation=0,
        variable_frame_rate=False,
        audio_stream_count=1,
        timecode=None,
        camera_make=None,
        camera_model=None,
        tags={},
        source=FactsSource.PROBE,
    )
    return replace(base, **changes)  # type: ignore[arg-type]
