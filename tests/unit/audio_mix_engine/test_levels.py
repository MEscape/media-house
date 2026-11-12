"""Level design: music and effects placed against the levelled dialogue, as profile data."""

from dataclasses import replace

import pytest

from media_house.modules.audio_mix_engine.domain.build import build_spec
from media_house.modules.audio_mix_engine.domain.levels import (
    IMPACT,
    OTHER,
    UI,
    WHOOSH,
    Placement,
    level_issues,
    place,
    sfx_class,
)
from media_house.modules.audio_mix_engine.domain.output import OutputFacts, validate_output
from media_house.modules.audio_mix_engine.domain.profile import (
    SHORTS_LEVELS,
    delivery_profile,
    level_targets,
    mix_profile,
)
from media_house.modules.audio_mix_engine.domain.result import Measurement
from media_house.modules.audio_mix_engine.domain.values import Severity
from tests.unit.audio_mix_engine import fixtures as f

REF = -18.0
SHORTS = SHORTS_LEVELS


def music(measured: float | None, **kw: object) -> Placement:
    base: dict[str, object] = {
        "clip_id": "m", "role": "music", "measured_lufs": measured, "gain_db": -30.0,
        "extra_db": 0.0, "bus_gain_db": -9.0, "duck_db": SHORTS.duck_depth_db,
    }  # fmt: skip
    base.update(kw)
    return Placement(**base)  # type: ignore[arg-type]


def effect(clip_id: str, measured: float | None, level_class: str, **kw: object) -> Placement:
    base: dict[str, object] = {
        "clip_id": clip_id, "role": "sfx", "measured_lufs": measured, "gain_db": -12.0,
        "extra_db": 0.0, "bus_gain_db": -6.0, "level_class": level_class,
    }  # fmt: skip
    base.update(kw)
    return Placement(**base)  # type: ignore[arg-type]


class TestMusic:
    def test_ducked_music_lands_its_target_under_speech_and_comes_up_in_the_gaps(self) -> None:
        lv = place(music(-14.0), reference_lufs=REF, targets=SHORTS)

        # in the mix: clip gain + trim + bus = gap level; the speech duck takes it under speech
        heard_in_gaps = -14.0 + (-30.0 + lv.trim_db) + -9.0
        assert heard_in_gaps == pytest.approx(REF - SHORTS.music_in_gaps_lu)
        assert heard_in_gaps - SHORTS.duck_depth_db == pytest.approx(
            REF - SHORTS.music_under_speech_lu
        )
        # without buses or ducking: the under-speech level
        assert -14.0 + lv.static_gain_db == pytest.approx(REF - SHORTS.music_under_speech_lu)

    def test_music_that_plays_under_speech_without_a_duck_stays_at_the_under_speech_level(
        self,
    ) -> None:
        lv = place(music(-14.0, duck_db=None), reference_lufs=REF, targets=SHORTS)

        assert lv.target_lufs == REF - SHORTS.music_under_speech_lu

    def test_music_where_nobody_speaks_may_come_up(self) -> None:
        free = music(-14.0, duck_db=None, under_speech=False)

        lv = place(free, reference_lufs=REF, targets=SHORTS)

        assert lv.target_lufs == REF - SHORTS.music_in_gaps_lu

    def test_how_loud_the_file_is_does_not_change_where_it_sits(self) -> None:
        quiet = place(music(-26.0), reference_lufs=REF, targets=SHORTS)
        loud = place(music(-8.0), reference_lufs=REF, targets=SHORTS)

        assert -26.0 + quiet.static_gain_db == pytest.approx(-8.0 + loud.static_gain_db)

    def test_a_persons_change_stays_on_top_of_the_level(self) -> None:
        plain = place(music(-14.0), reference_lufs=REF, targets=SHORTS)
        changed = place(
            music(-14.0, gain_db=-30.0, extra_db=3.0), reference_lufs=REF, targets=SHORTS
        )

        assert changed.trim_db == plain.trim_db  # the clip's gain already holds the +3
        assert changed.static_gain_db == plain.static_gain_db + 3.0

    def test_an_unmeasured_clip_keeps_its_compiled_gain(self) -> None:
        lv = place(music(None), reference_lufs=REF, targets=SHORTS)

        assert (lv.trim_db, lv.static_gain_db, lv.target_lufs) == (0.0, -39.0, None)


class TestEffects:
    def test_each_kind_has_its_own_distance_below_the_dialogue(self) -> None:
        heard = {
            c: -20.0 + place(effect(c, -20.0, c), reference_lufs=REF, targets=SHORTS).static_gain_db
            for c in (IMPACT, WHOOSH, UI)
        }

        assert heard == {IMPACT: REF - 4.0, WHOOSH: REF - 8.0, UI: REF - 10.0}
        assert heard[IMPACT] > heard[WHOOSH] > heard[UI]

    def test_effects_of_one_kind_sound_equally_loud_whatever_their_files(self) -> None:
        levels = [
            place(effect("a", -9.0, UI), reference_lufs=REF, targets=SHORTS),
            place(effect("b", -31.0, UI), reference_lufs=REF, targets=SHORTS),
        ]

        assert {lv.measured_lufs + lv.static_gain_db for lv in levels if lv.measured_lufs} == {
            REF - SHORTS.sfx_ui_lu
        }
        assert level_issues(levels, SHORTS) == ()

    def test_nothing_is_set_louder_than_the_dialogue(self) -> None:
        loud = replace(SHORTS, sfx_impact_lu=-6.0)  # a target above the voice

        lv = place(effect("a", -20.0, IMPACT), reference_lufs=REF, targets=loud)

        assert lv.target_lufs == REF - loud.sfx_cap_lu

    def test_a_file_too_quiet_to_be_raised_far_enough_is_reported(self) -> None:
        levels = [
            place(effect("a", -20.0, UI), reference_lufs=REF, targets=SHORTS),
            place(effect("b", -60.0, UI), reference_lufs=REF, targets=SHORTS),
        ]

        [issue] = level_issues(levels, SHORTS)
        assert issue.code == "sfx_levels_inconsistent" and issue.severity is Severity.WARNING

    def test_the_kind_comes_from_the_librarys_categories(self) -> None:
        assert sfx_class(("cinematic_hit",)) == IMPACT
        assert sfx_class(("fireworks",)) == IMPACT
        assert sfx_class(("swish_swipe", "whoosh")) == WHOOSH
        assert sfx_class(("pop",)) == UI
        assert sfx_class(("crowd_cheer_applause",)) == OTHER
        assert sfx_class(()) == OTHER


class TestProfiles:
    def test_a_short_has_its_own_balance_a_long_talking_head_keeps_music_further_back(
        self,
    ) -> None:
        talking = mix_profile("talking_head")

        shorts = level_targets(talking, delivery_profile("shorts"))
        long_form = level_targets(talking, delivery_profile("youtube"))

        assert shorts.music_under_speech_lu == 15.0 and long_form.music_under_speech_lu == 18.0
        assert shorts.sfx_impact_lu < long_form.sfx_impact_lu
        for t in (shorts, long_form):
            assert t.sfx_impact_lu < t.sfx_whoosh_lu < t.sfx_ui_lu
            assert t.music_under_speech_lu < t.max_music_gap_lu

    def test_the_under_speech_music_level_passes_the_profiles_own_masking_check(self) -> None:
        for profile in ("generic", "talking_head", "tutorial", "product_demo", "cinematic"):
            p = mix_profile(profile)
            assert p.levels.music_under_speech_lu >= p.min_dialogue_over_music_lu, profile


class TestSpecification:
    def test_with_levels_the_speech_duck_is_the_step_from_gap_to_under_speech_level(self) -> None:
        facts = f.facts_of()
        built = build_spec(
            facts,
            mix_profile("talking_head"),
            delivery_profile("shorts"),
            created_at=f.NOW,
            levels=SHORTS,
        )

        assert built.spec is not None
        speech = [d for d in built.spec.ducking if d.key == "speech"]
        assert speech and all(d.depth_db == SHORTS.duck_depth_db for d in speech)
        assert built.spec.validation.max_dialogue_over_music_lu == SHORTS.max_music_gap_lu

    def test_music_too_far_under_the_voice_is_flagged_as_inaudible(self) -> None:
        built = build_spec(
            f.facts_of(),
            mix_profile("talking_head"),
            delivery_profile("shorts"),
            created_at=f.NOW,
            levels=SHORTS,
        )
        assert built.spec is not None
        m = Measurement(-14.0, 6.0, -1.5, -1.6, 384000, 48000, 2, "pcm_s24le")

        def issues(music_lufs: float) -> set[str]:
            facts = OutputFacts(m, {}, (), -18.0, music_lufs, -200.0)
            return {i.code for i in validate_output(built.spec, facts)}  # type: ignore[arg-type]

        assert "music_inaudible" in issues(-45.0)  # the delivered Short: 27 LU under the voice
        assert "music_inaudible" not in issues(-33.0)
        assert "music_masks_dialogue" not in issues(-33.0)
