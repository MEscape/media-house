from dataclasses import replace
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.resolve_adapter.application.views import to_facts
from media_house.modules.timeline_builder.domain.model import CropKeyframe, CropPlan
from tests.unit.resolve_adapter import fixtures as f
from tests.unit.timeline_builder import fixtures as bf


def _key(frame: int, x: float, w: float, h: float) -> CropKeyframe:
    return CropKeyframe(
        source_time=Rational(frame, 25),
        timeline_time=Rational(frame, 25),
        timeline_frame=frame,
        x=x,
        y=0.0,
        w=w,
        h=h,
        interpolation="linear",
    )


def test_a_camera_beat_zooms_inside_the_window_whatever_its_analysis_is_called() -> None:
    t = bf.built()
    track = next(tr for tr in t.tracks if tr.video)
    clip = track.video[0]
    a, b = clip.start_frame, clip.end_frame
    window = CropPlan(
        crop_id="w", target="9:16", recommendation_id="fr_1", analysis_id="fr_x",
        strategy="track", keys=(_key(a, 0.3, 0.316, 1.0), _key(b, 0.3, 0.316, 1.0)),
    )  # fmt: skip
    beats = CropPlan(
        crop_id="c", target="source", recommendation_id=f"camera:{clip.clip_id}",
        analysis_id="beats:1.3:7/5", strategy="camera",
        keys=(_key(a, 0.0, 1.0, 1.0), _key(b, 0.1, 0.77, 0.77)),
    )  # fmt: skip
    moved = replace(track, video=(replace(clip, crops=(beats, window)), *track.video[1:]))
    timeline = replace(t, tracks=tuple(moved if tr is track else tr for tr in t.tracks))

    fact = next(c for c in to_facts(timeline, name=f.NAME).clips if c.clip_id == clip.clip_id)

    assert fact.crop is not None
    assert abs(fact.crop.w - 0.316) < 1e-6  # the 9:16 window, not the beat's frame
    assert fact.crop.motion  # the beat is the move inside it


def test_a_clip_at_normal_speed_fills_its_slot_whatever_its_source_times_round_to() -> None:
    t = bf.built()
    fps = Fraction(t.settings.frame_rate.numerator, t.settings.frame_rate.denominator)
    track = next(tr for tr in t.tracks if tr.video)
    clip = track.video[0]
    assert clip.source is not None and clip.time_map is None
    slot = clip.end_frame - clip.start_frame
    out = (slot - Fraction(3, 5)) / fps  # rounds to one frame short of the slot
    short = replace(
        clip.source,
        source_in=Rational(0, 1),
        source_in_frame=0,
        source_out=Rational(out.numerator, out.denominator),
    )
    moved = replace(track, video=(replace(clip, source=short), *track.video[1:]))
    timeline = replace(t, tracks=tuple(moved if tr is track else tr for tr in t.tracks))

    fact = next(c for c in to_facts(timeline, name=f.NAME).clips if c.clip_id == clip.clip_id)

    assert fact.source_frames == slot
