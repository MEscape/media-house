"""Exact timing across frame rates and sample rates, retiming, and one identity across videos."""

from dataclasses import replace

import pytest

from media_house.core.domain import Rational
from media_house.modules.edit_plan_compiler.domain.profile import CompilerConfig
from media_house.modules.timeline_builder.domain.facts import RetimeSegmentFact, SpecFacts
from media_house.modules.timeline_builder.domain.preflight import IdentityExpectation
from media_house.modules.timeline_builder.domain.profile import Capabilities
from media_house.modules.timeline_builder.domain.values import Severity
from tests.unit.edit_plan_compiler import fixtures as cf
from tests.unit.timeline_builder import fixtures as f


class TestFrameAndSampleAccuracy:
    @pytest.mark.parametrize(
        "rate",
        [Rational(24, 1), Rational(30, 1), Rational(24000, 1001), Rational(30000, 1001)],
    )
    def test_other_timeline_frame_rates_stay_exact_and_valid(self, rate: Rational) -> None:
        t = f.built(f.facts_of(CompilerConfig(timeline_frame_rate=rate)))

        assert t.settings.frame_rate == rate
        b = t.video_clip("v:pB")
        assert b is not None
        # frame = round(t * fps) on the EXACT time, never a float
        exact = round(4 * rate.numerator / rate.denominator)
        assert b.start_frame == exact
        assert t.validation.passed

    def test_audio_stays_sample_accurate_at_another_sample_rate(self) -> None:
        t = f.built(f.facts_of(CompilerConfig(sample_rate=44_100)))

        v = t.audio_clip("a:aW")
        assert v is not None
        assert v.sample_rate == 44_100 and v.start_sample == 4 * 44_100
        assert t.settings.sample_rate == 44_100

    def test_a_sample_rate_that_differs_from_the_timeline_is_refused(self) -> None:
        facts = f.change_audio(f.facts_of(), "a:aV", sample_rate=44_100)

        assert any("44100" in m for m in f.refused(facts))

    def test_a_source_at_another_rate_does_not_move_a_single_frame(self) -> None:
        plain = f.built()
        converted = f.built(f.change_asset(f.facts_of(), "rec1", frame_rate=Rational(30, 1)))

        assert [(c.clip_id, c.start_frame, c.end_frame) for c in converted.video_clips()] == [
            (c.clip_id, c.start_frame, c.end_frame) for c in plain.video_clips()
        ]

    def test_the_audio_and_the_picture_of_a_pair_are_never_allowed_to_drift(self) -> None:
        facts = f.change_audio(
            f.facts_of(), "a:aW", source_start=Rational(2005, 100), source_end=Rational(2405, 100)
        )

        assert any("other source time" in m for m in f.refused(facts))


class TestRetiming:
    def test_a_retime_the_application_must_apply_is_marked_so(self) -> None:
        # a VFX plan that was not rendered: the speed change is carried out by the application
        t = f.built(f.facts_of(vfx=cf.vfx("planned")))

        a = t.video_clip("v:pA")
        assert a is not None and a.time_map is not None and a.time_map.applied
        assert [s.rate for s in a.time_map.segments] == [Rational(1, 1), Rational(1, 2)]
        assert next(s for s in a.chain if s.stage.value == "time_map").applied
        assert a.source is not None and a.source.source_id == "rec1"

    def with_segments(self, *segments: RetimeSegmentFact) -> SpecFacts:
        facts = f.facts_of(vfx=cf.vfx("planned"))
        current = next(v for v in facts.video if v.item_id == "v:pA").retime
        assert current is not None
        return f.change_video(facts, "v:pA", retime=replace(current, segments=tuple(segments)))

    @staticmethod
    def seg(
        kind: str,
        t0: tuple[int, int],
        t1: tuple[int, int],
        s0: tuple[int, int],
        s1: tuple[int, int],
        rate: tuple[int, int],
    ) -> RetimeSegmentFact:
        return RetimeSegmentFact(
            kind=kind,
            timeline_start=Rational(*t0),
            timeline_end=Rational(*t1),
            source_start=Rational(*s0),
            source_end=Rational(*s1),
            rate=Rational(*rate),
            start_frame=round(t0[0] / t0[1] * 25),
            end_frame=round(t1[0] / t1[1] * 25),
        )

    def test_a_freeze_frame_holds_one_source_frame(self) -> None:
        facts = self.with_segments(
            self.seg("normal", (0, 1), (2, 1), (10, 1), (12, 1), (1, 1)),
            self.seg("freeze", (2, 1), (4, 1), (12, 1), (12, 1), (0, 1)),
        )

        t = f.built(facts)

        assert "invalid_time_map" not in f.codes(t)

    def test_reverse_playback_is_carried_and_can_be_declined_by_the_target(self) -> None:
        facts = self.with_segments(
            self.seg("normal", (0, 1), (2, 1), (10, 1), (12, 1), (1, 1)),
            self.seg("reverse", (2, 1), (4, 1), (14, 1), (12, 1), (1, 1)),
        )

        ok = f.built(facts)
        declined = f.built(facts, caps=Capabilities(reverse=False))

        assert ok.video_clip("v:pA").time_map.has_reverse  # type: ignore[union-attr]
        assert "unsupported_reverse" not in f.codes(ok)
        assert (
            next(i for i in declined.issues if i.code == "unsupported_reverse").severity
            is Severity.UNSUPPORTED
        )

    def test_a_speed_ramp_can_be_declined_by_the_target(self) -> None:
        t = f.built(f.facts_of(vfx=cf.vfx("planned")), caps=Capabilities(speed_ramps=False))

        assert "unsupported_speed_ramp" in f.codes(t)

    def test_a_time_map_with_a_hole_is_refused(self) -> None:
        facts = self.with_segments(
            self.seg("normal", (0, 1), (1, 1), (10, 1), (11, 1), (1, 1)),
            self.seg("normal", (2, 1), (4, 1), (12, 1), (14, 1), (1, 1)),
        )

        assert any("time map" in m for m in f.refused(facts))

    def test_a_segment_whose_source_does_not_match_its_rate_is_refused(self) -> None:
        facts = self.with_segments(
            self.seg("normal", (0, 1), (2, 1), (10, 1), (12, 1), (1, 1)),
            self.seg("slow", (2, 1), (4, 1), (12, 1), (14, 1), (1, 2)),
        )

        assert any("rate x timeline span" in m for m in f.refused(facts))


class TestOneIdentityAcrossVideos:
    def second_video(self) -> SpecFacts:
        """Another video of the same channel: other graphic, other captions, same style."""
        graphics = cf.graphics()
        other = replace(
            graphics.graphics[0],
            graphic_id="g9",
            start_frame=100,
            end_frame=150,
            timeline_start=Rational(4, 1),
        )
        return f.facts_of(graphics=replace(graphics, graphics=(other,)))

    def test_two_videos_of_a_channel_carry_the_same_identity_reference(self) -> None:
        a, b = f.built(), f.built(self.second_video())

        assert a.identity == b.identity
        assert a.build.build_hash != b.build.build_hash  # the videos differ, the identity does not
        assert a.timeline_id != b.timeline_id or a.build.spec_hash != b.build.spec_hash

    def test_the_caller_can_demand_the_channels_identity_of_every_video(self) -> None:
        want = IdentityExpectation("mesc", 2, cf.STYLE_FP)

        f.built(f.facts_of(), expect=want)
        f.built(self.second_video(), expect=want)

    def test_a_video_made_with_another_identity_is_refused_not_adapted(self) -> None:
        facts = f.facts_of()
        stranger = replace(facts, creator=replace(facts.creator, identity_id="someone_else"))

        assert any(
            "someone_else" in m for m in f.refused(stranger, expect=IdentityExpectation("mesc"))
        )

    def test_typography_and_design_choices_are_the_same_in_every_video(self) -> None:
        a, b = f.built(), f.built(self.second_video())

        assert a.identity.fonts == b.identity.fonts
        assert a.identity.zones == b.identity.zones
        assert {x.identity for x in a.video_clips() if x.identity} == {
            (("graphic_kind", "lower_third"),),
            (("caption_look", "word_group"),),
        }
        assert {x.identity for x in b.video_clips() if x.identity} == {
            x.identity for x in a.video_clips() if x.identity
        }
