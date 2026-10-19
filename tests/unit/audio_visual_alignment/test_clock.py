"""The common clock: signs, no double application, metadata-derived corrections, the lag probe."""

import pytest

from media_house.modules.audio_visual_alignment.domain.clock import (
    Clock,
    build_corrections,
    probe_lag,
)
from media_house.modules.audio_visual_alignment.domain.config import AlignmentProfile
from media_house.modules.audio_visual_alignment.domain.engine import align
from media_house.modules.audio_visual_alignment.domain.errors import InvalidAlignmentConfig
from media_house.modules.audio_visual_alignment.domain.inputs import ClockFacts, MouthSample
from media_house.modules.audio_visual_alignment.domain.model import ClockCorrection, LagProbe
from media_house.modules.audio_visual_alignment.domain.values import (
    CorrectionSource,
    FlagKind,
    ProbeState,
)
from tests.support.avl_facts import (
    activity,
    audio,
    blocks,
    inputs,
    mouth_following,
    segment,
    shot,
    still,
    video,
)
from tests.unit.audio_visual_alignment.helpers import run

PROFILE = AlignmentProfile()
TOTAL = 300.0


def corrections(
    first_frame: float | None, clock: ClockFacts | None, shift: float | None = None
) -> tuple[ClockCorrection, ...]:
    return build_corrections(
        clock, video([shot("s", 0, 5)], first_frame=first_frame), shift, PROFILE
    )


class TestVideoOrigin:
    def test_an_analysis_that_already_uses_the_container_timeline_is_left_alone(self) -> None:
        # MP4 measured with FFmpeg: container starts at 0.4768, video at 1.0, so the first frame
        # is 0.5232 after the container start and the analysis reports exactly that.
        facts = ClockFacts(container_start=0.4768, video_first_pts=1.0)

        video_fix, _ = corrections(0.5232, facts)

        assert video_fix.seconds == 0.0
        assert video_fix.source is CorrectionSource.INSPECTION
        assert video_fix.expected_seconds == pytest.approx(0.5232)

    def test_an_analysis_that_started_at_zero_is_shifted_to_the_container_timeline(self) -> None:
        # MPEG-TS measured with FFmpeg: container starts at 1.876778, video at 2.4, yet the
        # decoder reported the first frame at 0.0. The first frame IS 0.523222 s after the start.
        facts = ClockFacts(container_start=1.876778, video_first_pts=2.4)

        video_fix, _ = corrections(0.0, facts)

        assert video_fix.seconds == pytest.approx(0.523222)
        assert Clock(video_shift=video_fix.seconds).video(0.0) == pytest.approx(0.523222)

    def test_the_sign_is_common_equals_upstream_plus_correction(self) -> None:
        facts = ClockFacts(container_start=0.0, video_first_pts=0.0)

        video_fix, _ = corrections(2.0, facts)  # the analysis is 2 s LATE

        assert video_fix.seconds == pytest.approx(-2.0)
        assert Clock(video_shift=video_fix.seconds).video(2.0) == pytest.approx(0.0)

    def test_without_an_inspection_nothing_is_corrected_and_that_is_said(self) -> None:
        video_fix, _ = corrections(0.5, None)

        assert video_fix.seconds == 0.0
        assert video_fix.source is CorrectionSource.NONE

        doc = run(inputs(audio([segment("1", 1, 3)]), video([shot("s", 0, 5)], first_frame=0.5)))
        assert FlagKind.VIDEO_ORIGIN_UNVERIFIED in {f.kind for f in doc.flags}

    def test_rounding_noise_is_not_an_offset(self) -> None:
        facts = ClockFacts(container_start=0.0, video_first_pts=0.0)

        video_fix, _ = corrections(0.0004, facts)

        assert video_fix.seconds == 0.0


class TestAudioShift:
    def test_a_supplied_shift_is_applied_exactly_once(self) -> None:
        a = audio([segment("1", 10.0, 14.0)])
        v = video([shot("s", 0, 60)], still(0, 60))

        doc = run(inputs(a, v, shift=2.5))

        assert doc.speech_sources[0].start == pytest.approx(12.5)
        assert doc.speech_sources[0].end == pytest.approx(16.5)
        assert next(c for c in doc.sync.corrections if c.stream == "audio").source is (
            CorrectionSource.SUPPLIED
        )

    def test_the_original_upstream_times_stay_on_the_picture_side(self) -> None:
        clock = ClockFacts(container_start=1.876778, video_first_pts=2.4)
        v = video([shot("s", 0, 30)], still(0, 30), first_frame=0.0)

        doc = run(inputs(audio([segment("1", 5, 9)]), v, clock=clock))

        part = doc.speech_sources[0].shots[0]
        assert part.video_range.start.seconds == 0.0  # the analysis' own FrameTime is untouched
        assert doc.video_correction == pytest.approx(0.523222)


class TestLagProbe:
    def probe(
        self,
        lag: float,
        *,
        mouth: list[MouthSample] | None = None,
        profile: AlignmentProfile = PROFILE,
    ) -> LagProbe:
        speech = blocks(TOTAL)
        act = activity(TOTAL, lambda t: any(a <= t < b for a, b in speech))
        samples = mouth if mouth is not None else mouth_following(speech, TOTAL, lag)
        return probe_lag(act, samples, Clock(), profile)

    def test_synchronised_streams_are_consistent_at_zero_lag(self) -> None:
        result = self.probe(0.0)

        assert result.state is ProbeState.CONSISTENT
        assert result.lag_seconds == 0.0
        assert result.correlation is not None and result.correlation > 0.8

    @pytest.mark.parametrize("lag", [1.5, -2.0])
    def test_a_real_offset_is_indicated_with_the_right_sign(self, lag: float) -> None:
        result = self.probe(lag)

        assert result.state is ProbeState.OFFSET_INDICATED
        assert result.lag_seconds == pytest.approx(lag, abs=0.5)

    def test_a_still_mouth_proves_nothing_either_way(self) -> None:
        result = self.probe(0.0, mouth=still(0, TOTAL))

        assert result.state is ProbeState.INSUFFICIENT

    def test_too_little_evidence_is_insufficient_not_a_guess(self) -> None:
        speech = blocks(TOTAL)
        act = activity(TOTAL, lambda t: any(a <= t < b for a, b in speech))

        result = probe_lag(act, mouth_following(speech, 12.0, 0.0), Clock(), PROFILE)

        assert result.state is ProbeState.INSUFFICIENT

    def test_a_periodic_signal_cannot_name_one_lag(self) -> None:
        period = [(float(t), t + 1.0) for t in range(2, 290, 2)]  # one second on, one off
        act = activity(TOTAL, lambda t: any(a <= t < b for a, b in period))

        result = probe_lag(act, mouth_following(period, TOTAL, 0.0), Clock(), PROFILE)

        assert result.state is ProbeState.AMBIGUOUS

    def test_the_probe_is_never_applied_to_any_time(self) -> None:
        speech = blocks(TOTAL)
        a = audio(
            [segment("1", 10, 14)],
            duration=TOTAL,
            speaking=lambda t: any(x <= t < y for x, y in speech),
        )
        v = video([shot("s", 0, TOTAL)], mouth_following(speech, TOTAL, 2.0), duration=TOTAL)

        doc = run(inputs(a, v))

        assert doc.sync.probe.state is ProbeState.OFFSET_INDICATED
        assert doc.speech_sources[0].start == 10.0  # unchanged
        assert FlagKind.SYNC_OFFSET_INDICATED in {f.kind for f in doc.flags}
        assert all(c.seconds == 0.0 for c in doc.sync.corrections)

    def test_a_lag_step_that_is_not_a_multiple_of_the_hop_is_refused(self) -> None:
        bad = AlignmentProfile(probe_lag_step_seconds=0.7)

        with pytest.raises(InvalidAlignmentConfig):
            self.probe(0.0, profile=bad)

    def test_missing_signals_are_reported_as_insufficient(self) -> None:
        assert probe_lag(None, [MouthSample(0.0, 0.1)] * 5, Clock(), PROFILE).state is (
            ProbeState.INSUFFICIENT
        )


def test_align_reports_a_duration_mismatch_without_failing() -> None:
    doc = run(inputs(audio([segment("1", 1, 2)], duration=100.0), video([shot("s", 0, 60)])))

    assert FlagKind.DURATION_MISMATCH in {f.kind for f in doc.flags}
    assert align is not None
