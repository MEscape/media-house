"""The specification: derived from the timeline, deterministic, traceable, honest about gaps."""

from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction

import pytest

from media_house.core.domain import Rational
from media_house.modules.audio_mix_engine.domain.build import MixOverrides
from media_house.modules.audio_mix_engine.domain.errors import InvalidMixConfig, InvalidMixDocument
from media_house.modules.audio_mix_engine.domain.profile import (
    PROFILES,
    choose_delivery,
    choose_profile,
    delivery_profile,
    mix_profile,
)
from media_house.modules.audio_mix_engine.domain.spec import spec_from_json, spec_to_json
from media_house.modules.audio_mix_engine.domain.timebase import frame_to_sample, seconds_to_sample
from media_house.modules.audio_mix_engine.domain.values import (
    BusRole,
    DependencyState,
    Provenance,
    Severity,
)
from tests.unit.audio_mix_engine import fixtures as f


class TestStructure:
    def test_one_bus_per_role_in_use_and_the_master(self) -> None:
        spec = f.spec_of()

        assert [b.bus_id for b in spec.buses] == ["dialogue", "music", "sfx", "master"]
        assert spec.master.role is BusRole.MASTER and spec.master.parent is None
        assert all(b.parent == "master" for b in spec.buses if b.role is not BusRole.MASTER)

    def test_every_track_is_routed_to_the_bus_of_its_role(self) -> None:
        spec = f.spec_of()

        assert {t.track_id: t.bus_id for t in spec.tracks} == {
            "A1": "dialogue",
            "A3": "music",
            "A5": "sfx",
        }

    def test_clips_keep_the_timelines_ids_and_exact_positions(self) -> None:
        spec = f.spec_of()

        clip = spec.clip("a:aW")
        assert (
            clip is not None
            and clip.origin_ref == "a:aW"
            and clip.provenance is Provenance.COMPILED
        )
        assert (clip.start_sample, clip.end_sample) == (192000, 384000)
        assert (clip.source_in, clip.source_out) == (Rational(20, 1), Rational(24, 1))
        assert spec.timebase.sample_rate == 48000 and spec.timebase.duration_samples == 384000

    def test_one_stem_per_bus_that_stops_before_the_master(self) -> None:
        spec = f.spec_of()

        assert [(s.stem_id, s.bus_id, s.stage) for s in spec.stems] == [
            ("stem:dialogue", "dialogue", "post_bus"),
            ("stem:music", "music", "post_bus"),
            ("stem:sfx", "sfx", "post_bus"),
        ]

    def test_sources_name_the_original_and_what_was_chosen(self) -> None:
        spec = f.spec_of()

        rec1 = spec.source("rec1")
        assert rec1 is not None and rec1.original_asset_id == "rec1" and rec1.sample_rate == 48000

    def test_the_specification_names_no_editing_application(self) -> None:
        text = spec_to_json(f.spec_of()).lower()

        assert "davinci" not in text and "resolve" not in text


class TestInstructionsFromTheTimeline:
    def test_the_sound_designs_duck_is_used_and_nothing_automatic_is_added(self) -> None:
        spec = f.spec_of()

        [duck] = spec.ducking
        assert duck.provenance is Provenance.COMPILED and duck.clip_ids == ("a:music:seg1",)
        assert duck.depth_db == 10.0 and (duck.attack_seconds, duck.release_seconds) == (0.1, 0.3)

    def test_a_negative_depth_is_read_as_a_reduction_and_said_so(self) -> None:
        built = f.built_of()

        assert any(
            i.code == "duck_depth_sign" and i.severity is Severity.INFO for i in built.issues
        )

    def test_the_crossfade_becomes_equal_power_fades_across_the_cut(self) -> None:
        spec = f.spec_of()

        out, inc = spec.clip("a:aV"), spec.clip("a:aW")
        assert out is not None and inc is not None
        # the window is 50 ms before and after the cut at sample 192000; the clips meet there,
        # so each fades over its own 50 ms
        assert out.fade_out == Rational(1, 20) and out.fade_curve == "equal_power"
        assert inc.fade_in == Rational(1, 20) and inc.fade_curve == "equal_power"
        [x] = spec.crossfades
        assert x.at_sample == 192000 and x.clip_ids == ("a:aV", "a:aW")

    def test_a_fade_the_clip_already_had_is_not_shortened(self) -> None:
        spec = f.spec_of()

        music = spec.clip("a:music:seg1")
        assert (
            music is not None
            and music.fade_in == Rational(1, 1)
            and music.fade_out == Rational(2, 1)
        )

    def test_without_the_sound_designs_ducking_the_profile_derives_one_from_the_voice(self) -> None:
        facts = f.without_instructions(f.facts_of(), "duck")

        spec = f.spec_of(facts)

        [duck] = spec.ducking
        assert duck.provenance is Provenance.AUTOMATIC and duck.key == "speech"
        assert (duck.start_sample, duck.end_sample) == (0, 384000)  # the two clips touch: one range
        assert duck.depth_db == mix_profile("talking_head").ducking.depth_db

    def test_speech_spans_from_the_analysis_replace_the_clip_spans(self) -> None:
        facts = f.without_instructions(f.facts_of(), "duck")

        spec = f.spec_of(facts, speech_spans=((48000, 96000), (240000, 288000)))

        assert [(d.start_sample, d.end_sample) for d in spec.ducking] == [
            (48000, 96000),
            (240000, 288000),
        ]

    def test_speech_gaps_shorter_than_the_hold_keep_the_music_down(self) -> None:
        facts = f.without_instructions(f.facts_of(), "duck")
        hold = int(mix_profile("talking_head").ducking.hold_seconds * 48000)

        spec = f.spec_of(facts, speech_spans=((0, 48000), (48000 + hold - 1, 96000 + hold)))

        assert len(spec.ducking) == 1

    def test_a_profile_without_auto_duck_adds_none(self) -> None:
        facts = f.without_instructions(f.facts_of(), "duck")
        profile = replace(mix_profile("talking_head"), auto_duck=False)

        assert f.spec_of(facts, profile=profile).ducking == ()

    def test_an_instruction_the_engine_does_not_know_is_reported_not_dropped_silently(self) -> None:
        facts = f.facts_of()
        odd = replace(facts.instructions[0], mix_id="x:odd", kind="warp")
        built = f.built_of(replace(facts, instructions=(*facts.instructions, odd)))

        assert any(
            i.code == "instruction_unknown" and i.element_id == "x:odd" for i in built.issues
        )


class TestWhatCannotBeMixed:
    def test_a_clip_whose_sound_is_unavailable_is_left_out_and_listed_as_missing(self) -> None:
        facts = f.facts_of()
        sources = tuple(
            replace(s, available=False) if s.asset_id == "whoosh1" else s for s in facts.sources
        )

        built = f.built_of(replace(facts, sources=sources))

        assert built.spec is not None and built.spec.clip("a:sfx:sfx:1") is None
        [dep] = built.spec.dependencies
        assert dep.reference == "whoosh1" and dep.state is DependencyState.MISSING
        assert dep.required_by == ("a:sfx:sfx:1",)
        assert any(i.code == "clip_unavailable" for i in built.issues)
        assert "sfx" not in [b.bus_id for b in built.spec.buses]

    def test_a_timeline_with_no_sound_has_nothing_to_mix(self) -> None:
        facts = replace(f.facts_of(), clips=())

        built = f.built_of(facts)

        assert built.spec is None and [i.code for i in built.errors] == ["nothing_to_mix"]


class TestRevisions:
    def test_an_override_changes_a_bus_and_a_clip_and_is_recorded_as_a_persons(self) -> None:
        spec = f.spec_of(overrides=MixOverrides((("music", -3.0),), (("a:aW", 2.0),)))

        music = spec.bus("music")
        assert (
            music is not None
            and music.gain_db == mix_profile("talking_head").bus(BusRole.MUSIC).gain_db - 3.0
        )
        clip = spec.clip("a:aW")
        assert clip is not None and clip.gain_db == 2.0 and clip.provenance is Provenance.HUMAN
        assert spec.provenance.overrides == ("bus:music=-3", "clip:a:aW=2")

    def test_the_hash_is_deterministic_and_ignores_the_clock(self) -> None:
        a = f.spec_of()
        b = f.spec_of()

        assert (
            a.content_hash == b.content_hash
            and a.specification_id == f"mixspec-{a.content_hash[:16]}"
        )
        assert replace(a, created_at="later").content_hash == a.content_hash

    def test_any_change_of_what_is_mixed_changes_the_hash(self) -> None:
        base = f.spec_of().content_hash

        assert f.spec_of(overrides=MixOverrides((("music", -1.0),))).content_hash != base
        assert f.spec_of(delivery=delivery_profile("broadcast_r128")).content_hash != base
        assert f.spec_of(profile=mix_profile("cinematic")).content_hash != base


class TestProfiles:
    def test_the_creator_identitys_format_profile_chooses_the_mix_and_delivery(self) -> None:
        assert choose_profile(None, "talking_head").key == "talking_head"
        assert choose_profile(None, "something_else").key == "generic"
        assert choose_delivery(None, "shorts_meme").key == "shorts"
        assert choose_delivery(None, "talking_head").key == "youtube"

    def test_an_explicit_choice_wins(self) -> None:
        assert choose_profile("cinematic", "talking_head").key == "cinematic"
        assert choose_delivery("broadcast_r128", "shorts_meme").key == "broadcast_r128"

    def test_unknown_profiles_are_refused_with_the_valid_names(self) -> None:
        with pytest.raises(InvalidMixConfig, match="talking_head"):
            mix_profile("loud")
        with pytest.raises(InvalidMixConfig, match="youtube"):
            delivery_profile("vhs")

    def test_profiles_differ_in_how_the_music_sits(self) -> None:
        levels = {p.key: p.bus(BusRole.MUSIC).gain_db for p in PROFILES}

        assert len(set(levels.values())) > 2

    def test_no_loudness_target_is_the_same_for_every_delivery(self) -> None:
        assert (
            delivery_profile("youtube").integrated_lufs
            != delivery_profile("broadcast_r128").integrated_lufs
        )

    def test_the_limiter_ceiling_stays_under_the_deliverys_true_peak(self) -> None:
        spec = f.spec_of(delivery=delivery_profile("broadcast_r128"))

        assert spec.master.limiter is not None
        assert spec.master.limiter.ceiling_db <= spec.loudness.true_peak_dbtp - 0.5


class TestTimebase:
    def test_a_frame_is_the_exact_sample_where_it_starts(self) -> None:
        assert frame_to_sample(25, Rational(25, 1), 48000) == 48000

    def test_a_fractional_frame_rate_is_not_rounded_early(self) -> None:
        # 30000/1001 fps: frame 30 starts at 1.001 s
        assert frame_to_sample(30, Rational(30000, 1001), 48000) == 48048
        assert frame_to_sample(30 * 1000, Rational(30000, 1001), 48000) == 48048000

    def test_seconds_round_half_to_even_on_the_exact_value(self) -> None:
        assert seconds_to_sample(Fraction(1, 3), 48000) == 16000


class TestDocument:
    def test_it_reads_back_equal(self) -> None:
        spec = f.spec_of()

        assert spec_from_json(spec_to_json(spec)) == spec

    def test_the_text_is_deterministic(self) -> None:
        assert spec_to_json(f.spec_of()) == spec_to_json(f.spec_of())

    @pytest.mark.parametrize(
        ("mutate", "message"),
        [
            (lambda d: d.update(document_type="built_timeline"), "not an audio mix"),
            (lambda d: d.update(schema_version=9), "schema version"),
            (lambda d: d.update(surprise=1), "unknown fields"),
            (lambda d: d.pop("loudness"), "missing field"),
        ],
    )
    def test_a_document_that_is_not_exactly_the_shape_is_refused(
        self, mutate: Callable[[dict[str, object]], object], message: str
    ) -> None:
        import json

        body = json.loads(spec_to_json(f.spec_of()))
        mutate(body)

        with pytest.raises(InvalidMixDocument, match=message):
            spec_from_json(json.dumps(body))

    def test_not_json_is_refused(self) -> None:
        with pytest.raises(InvalidMixDocument, match="JSON"):
            spec_from_json("{")
