"""Frame rhythm and stream continuity measured from packet timestamps."""

import pytest

from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.timing import (
    MAX_POSITIONS,
    Packet,
    analyze_audio_packets,
    analyze_video_packets,
)
from media_house.modules.media_inspection.domain.values import FrameRateMode
from tests.support.inspection_fakes import packets

CONFIG = InspectionConfig()
TICK = 1 / 90_000
FRAME = 1 / 30


def at_30fps(count: int = 90) -> list[float | None]:
    return [i * FRAME for i in range(count)]


class TestConstantRate:
    def test_even_spacing_is_constant(self) -> None:
        timing = analyze_video_packets(packets(at_30fps()), TICK, CONFIG)

        assert timing.mode is FrameRateMode.CONSTANT
        assert timing.packet_count == 90
        assert timing.median_interval == pytest.approx(FRAME)
        assert timing.measured_duration == pytest.approx(3.0)
        assert timing.measured_frame_rate == pytest.approx(30.0)
        assert timing.irregular_intervals == timing.dropped_frames == 0
        assert timing.discontinuity_count == 0

    def test_rounding_to_a_millisecond_time_base_is_not_variability(self) -> None:
        # 29.97 fps stored in a 1 ms time base alternates 33 / 34 ms
        times: list[float | None] = [round(i * 1001 / 30000, 3) for i in range(300)]

        timing = analyze_video_packets(packets(times), 0.001, CONFIG)

        assert timing.mode is FrameRateMode.CONSTANT

    def test_bit_rate_comes_from_the_packet_sizes(self) -> None:
        timing = analyze_video_packets(packets(at_30fps()), TICK, CONFIG)

        assert timing.measured_bit_rate == round(90 * 1000 * 8 / 3.0)

    def test_keyframes_and_their_longest_interval(self) -> None:
        timing = analyze_video_packets(packets(at_30fps(), keyframe_every=30), TICK, CONFIG)

        assert timing.keyframe_count == 3
        assert timing.max_keyframe_interval == 30

    def test_reordered_packets_are_recognised_and_do_not_break_the_rhythm(self) -> None:
        # decode order I P B B ...: presentation times do not increase in packet order
        times: list[float | None] = [
            0.0,
            3 * FRAME,
            FRAME,
            2 * FRAME,
            6 * FRAME,
            4 * FRAME,
            5 * FRAME,
            7 * FRAME,
        ]

        timing = analyze_video_packets(packets(times), TICK, CONFIG)

        assert timing.reordered
        assert timing.mode is FrameRateMode.CONSTANT
        assert timing.irregular_intervals == 0

    def test_a_single_frame_or_nothing_has_no_rhythm(self) -> None:
        assert analyze_video_packets(packets([0.0]), TICK, CONFIG).mode is FrameRateMode.UNKNOWN
        empty = analyze_video_packets([], TICK, CONFIG)
        assert empty.mode is FrameRateMode.UNKNOWN
        assert empty.measured_duration is None


class TestVariableRate:
    def test_alternating_spacing_is_variable(self) -> None:
        times: list[float | None] = [i * FRAME + (0.01 if i % 2 else 0.0) for i in range(60)]

        timing = analyze_video_packets(packets(times), 0.001, CONFIG)

        assert timing.mode is FrameRateMode.VARIABLE
        assert timing.irregular_intervals >= 29  # the median interval itself counts as regular
        assert timing.min_interval == pytest.approx(FRAME - 0.01)
        assert timing.max_interval == pytest.approx(FRAME + 0.01)

    def test_a_single_odd_interval_is_not_a_variable_rate(self) -> None:
        times = [i * FRAME + (0.012 if i >= 50 else 0.0) for i in range(100)]

        timing = analyze_video_packets(packets(list(times)), TICK, CONFIG)

        assert timing.mode is FrameRateMode.CONSTANT
        assert timing.irregular_intervals == 1

    def test_the_threshold_is_configurable(self) -> None:
        shifts = {20: 0.012, 40: 0.024, 60: 0.036}  # three odd intervals in 99
        times: list[float | None] = [
            i * FRAME + max((s for at, s in shifts.items() if i >= at), default=0.0)
            for i in range(100)
        ]
        recorded = packets(times)

        assert analyze_video_packets(recorded, TICK, CONFIG).mode is FrameRateMode.VARIABLE
        lenient = InspectionConfig(variable_rate_fraction=0.1)
        assert analyze_video_packets(recorded, TICK, lenient).mode is FrameRateMode.CONSTANT


class TestHolesAndDamage:
    def test_whole_missing_frames_are_counted_as_dropped(self) -> None:
        times = [t for i, t in enumerate(at_30fps(120)) if i not in {30, 31}]

        timing = analyze_video_packets(packets(times), TICK, CONFIG)

        assert timing.dropped_frames == 2
        assert timing.discontinuity_count == 0
        assert timing.mode is FrameRateMode.CONSTANT  # one gap is damage, not a variable rate

    def test_a_long_hole_is_a_discontinuity_with_its_position(self) -> None:
        times = [t for i, t in enumerate(at_30fps(150)) if not 30 <= i < 90]

        timing = analyze_video_packets(packets(times), TICK, CONFIG)

        assert timing.discontinuity_count == 1
        assert len(timing.discontinuity_positions) == 1
        assert timing.discontinuity_positions[0] == pytest.approx(29 * FRAME)
        assert timing.dropped_frames == 0

    def test_only_the_first_positions_are_kept_but_the_count_is_exact(self) -> None:
        times: list[float | None] = []
        for block in range(15):
            times += [block * 10.0 + i * FRAME for i in range(5)]  # blocks 10 s apart

        timing = analyze_video_packets(packets(times), TICK, CONFIG)

        assert timing.discontinuity_count == 14
        assert len(timing.discontinuity_positions) == MAX_POSITIONS

    def test_duplicate_and_missing_timestamps_are_counted(self) -> None:
        times = at_30fps(60)
        times[10] = times[9]
        times[20] = None

        timing = analyze_video_packets(packets(times), TICK, CONFIG)

        assert timing.duplicate_timestamps == 1
        assert timing.missing_timestamps == 1


class TestAudioContinuity:
    @staticmethod
    def audio_packets(starts: list[float], duration: float = 0.02) -> list[Packet]:
        return [Packet(t, t, duration, 100, keyframe=True) for t in starts]

    def test_back_to_back_packets_have_no_gaps(self) -> None:
        timing = analyze_audio_packets(
            self.audio_packets([i * 0.02 for i in range(100)]), 1 / 48_000, CONFIG
        )

        assert timing.gap_count == timing.overlap_count == 0
        assert timing.measured_duration == pytest.approx(2.0)
        assert timing.first_pts == 0.0

    def test_a_hole_between_packets_is_a_gap_with_its_size(self) -> None:
        starts = [i * 0.02 for i in range(50)] + [1.5 + i * 0.02 for i in range(50)]

        timing = analyze_audio_packets(self.audio_packets(starts), 1 / 48_000, CONFIG)

        assert timing.gap_count == 1
        assert timing.total_gap_seconds == pytest.approx(
            0.5
        )  # the first group ends at 1.0 s, the next starts at 1.5 s
        assert timing.discontinuity_count == 0  # under the one-second discontinuity limit

    def test_overlapping_packets_are_reported(self) -> None:
        starts = [0.0, 0.02, 0.03, 0.05]  # the third starts before the second ends

        timing = analyze_audio_packets(self.audio_packets(starts), 1 / 48_000, CONFIG)

        assert timing.overlap_count == 1

    def test_priming_before_zero_is_just_the_first_timestamp(self) -> None:
        starts = [-0.0232 + i * 0.0232 for i in range(10)]

        timing = analyze_audio_packets(self.audio_packets(starts, 0.0232), 1 / 44_100, CONFIG)

        assert timing.first_pts == pytest.approx(-0.0232)
        assert timing.gap_count == 0

    def test_a_very_long_hole_is_a_discontinuity(self) -> None:
        starts = [0.0, 0.02, 30.0, 30.02]

        timing = analyze_audio_packets(self.audio_packets(starts), 1 / 48_000, CONFIG)

        assert timing.discontinuity_count == 1
