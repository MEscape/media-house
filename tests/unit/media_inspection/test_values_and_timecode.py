import pytest

from media_house.modules.media_inspection.domain.timecode import Timecode
from media_house.modules.media_inspection.domain.values import Provenance, Rational, Sourced
from media_house.shared.errors import InvariantViolation

NTSC = Rational(30000, 1001)
PAL = Rational(25, 1)


class TestRational:
    def test_keeps_the_exact_ntsc_rate_instead_of_rounding_it(self) -> None:
        rate = Rational.parse("30000/1001")

        assert rate == NTSC
        assert rate is not None
        assert round(rate.value, 3) == 29.97

    def test_reduces_to_lowest_terms(self) -> None:
        assert Rational.parse("60/2") == Rational(30, 1)
        assert str(Rational(50, 4)) == "25/2"

    @pytest.mark.parametrize("text", ["16:9", "16/9"])
    def test_reads_ratios_written_either_way(self, text: str) -> None:
        assert Rational.parse(text) == Rational(16, 9)

    @pytest.mark.parametrize("text", ["0/0", "N/A", "", "abc", "1/0", "-1/2", "1/2/3", None, 25])
    def test_unusable_text_is_none_never_a_guess(self, text: object) -> None:
        assert Rational.parse(text) is None

    def test_a_bare_number_is_a_whole_rate(self) -> None:
        assert Rational.parse("25") == PAL

    def test_rejects_a_zero_denominator(self) -> None:
        with pytest.raises(InvariantViolation):
            Rational(1, 0)


class TestSourced:
    def test_known_values_carry_their_origin(self) -> None:
        assert Sourced.declared(5).provenance is Provenance.DECLARED
        assert Sourced.detected(5).provenance is Provenance.DETECTED
        assert Sourced.inferred(5).provenance is Provenance.INFERRED
        assert Sourced.declared(5).known

    def test_unknown_has_no_value(self) -> None:
        unknown = Sourced[int].unknown()

        assert unknown.value is None
        assert not unknown.known

    def test_a_value_cannot_be_called_unknown_and_unknown_cannot_hold_one(self) -> None:
        with pytest.raises(InvariantViolation):
            Sourced(5, Provenance.UNKNOWN)
        with pytest.raises(InvariantViolation):
            Sourced(None, Provenance.DECLARED)


class TestTimecode:
    def test_parses_non_drop_and_drop_frame(self) -> None:
        assert Timecode.parse("10:00:00:00") == Timecode(10, 0, 0, 0, drop_frame=False)
        assert Timecode.parse("01:02:03;04") == Timecode(1, 2, 3, 4, drop_frame=True)

    @pytest.mark.parametrize("text", ["25:00:00:00", "10:61:00:00", "nonsense", "", None, "1:2:3"])
    def test_rejects_things_that_are_not_timecodes(self, text: object) -> None:
        assert Timecode.parse(text) is None

    def test_round_trips_through_text(self) -> None:
        assert str(Timecode(1, 2, 3, 4, drop_frame=True)) == "01:02:03;04"
        assert str(Timecode(10, 0, 0, 0)) == "10:00:00:00"

    def test_a_frame_that_does_not_exist_at_the_rate_is_a_problem(self) -> None:
        assert Timecode(0, 0, 0, 29).problem_for(PAL) is not None
        assert Timecode(0, 0, 0, 24).problem_for(PAL) is None

    def test_drop_frame_only_exists_at_29_97_and_59_94(self) -> None:
        drop = Timecode(0, 1, 0, 2, drop_frame=True)

        assert drop.problem_for(NTSC) is None
        assert drop.problem_for(PAL) is not None
        assert drop.problem_for(Rational(30, 1)) is not None

    def test_drop_frame_numbers_skipped_at_the_start_of_most_minutes_are_invalid(self) -> None:
        assert Timecode(0, 1, 0, 0, drop_frame=True).problem_for(NTSC) is not None
        assert Timecode(0, 1, 0, 1, drop_frame=True).problem_for(NTSC) is not None
        assert (
            Timecode(0, 10, 0, 0, drop_frame=True).problem_for(NTSC) is None
        )  # every tenth minute

    def test_frame_numbers_count_real_frames(self) -> None:
        assert Timecode(0, 0, 1, 0).frame_number(PAL) == 25
        # 00:01:00;02 is the first existing frame of minute one: frame 1800, two numbers dropped
        assert Timecode(0, 1, 0, 2, drop_frame=True).frame_number(NTSC) == 1800
        assert Timecode(0, 10, 0, 0, drop_frame=True).frame_number(NTSC) == 17982

    def test_position_on_the_wall_clock(self) -> None:
        assert Timecode(1, 0, 0, 0).seconds_at(PAL) == pytest.approx(3600.0)
        assert Timecode(0, 10, 0, 0, drop_frame=True).seconds_at(NTSC) == pytest.approx(
            600.0, abs=0.01
        )
