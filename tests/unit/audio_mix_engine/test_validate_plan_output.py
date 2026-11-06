"""Validation of a specification, what must be re-rendered when something changes, and the
judgement of a finished mix."""

from dataclasses import replace

import pytest

from media_house.modules.audio_mix_engine.domain.output import OutputFacts, validate_output
from media_house.modules.audio_mix_engine.domain.plan import (
    dialogue_trims,
    loudness_correction,
    mix_hash,
    stem_hash,
)
from media_house.modules.audio_mix_engine.domain.result import Measurement, OutputIssue
from media_house.modules.audio_mix_engine.domain.spec import (
    AudioMixSpecification,
    Automation,
    GainPoint,
    Loudness,
)
from media_house.modules.audio_mix_engine.domain.validate import (
    ALL_OPERATIONS,
    check_capabilities,
    required_operations,
    validate_spec,
)
from media_house.modules.audio_mix_engine.domain.values import (
    Certainty,
    Interpolation,
    Provenance,
    Severity,
)
from tests.unit.audio_mix_engine import fixtures as f


def codes(spec: AudioMixSpecification, severity: Severity | None = None) -> set[str]:
    return {i.code for i in validate_spec(spec) if severity is None or i.severity is severity}


class TestSpecificationValidation:
    def test_a_specification_made_from_a_timeline_has_no_errors(self) -> None:
        assert codes(f.spec_of(), Severity.ERROR) == set()

    def test_duplicate_ids_are_errors(self) -> None:
        spec = f.spec_of()

        broken = replace(spec, clips=(*spec.clips, spec.clips[0]))

        assert "duplicate_clip" in codes(broken, Severity.ERROR)

    def test_a_track_routed_nowhere_is_an_error(self) -> None:
        spec = f.spec_of()
        tracks = (replace(spec.tracks[0], bus_id="nowhere"), *spec.tracks[1:])

        assert "track_unrouted" in codes(replace(spec, tracks=tracks), Severity.ERROR)

    def test_a_bus_that_feeds_a_missing_bus_is_an_error(self) -> None:
        spec = f.spec_of()
        buses = (replace(spec.buses[0], parent="ghost"), *spec.buses[1:])

        assert "bus_destination" in codes(replace(spec, buses=buses), Severity.ERROR)

    def test_a_bus_that_feeds_another_bus_would_be_a_chain_the_engine_does_not_build(self) -> None:
        spec = f.spec_of()
        buses = (replace(spec.buses[0], parent="music"), *spec.buses[1:])

        assert "bus_destination" in codes(replace(spec, buses=buses), Severity.ERROR)

    def test_two_masters_or_none_is_an_error(self) -> None:
        spec = f.spec_of()

        assert "master_bus" in codes(replace(spec, buses=spec.buses[:-1]), Severity.ERROR)

    def test_a_clip_beyond_the_timeline_is_an_error(self) -> None:
        spec = f.spec_of()
        clip = replace(spec.clips[0], end_sample=spec.timebase.duration_samples + 5000)

        assert "clip_beyond_timeline" in codes(
            replace(spec, clips=(clip, *spec.clips[1:])), Severity.ERROR
        )

    def test_a_clip_that_plays_a_source_not_listed_is_an_error(self) -> None:
        spec = f.spec_of()
        clip = replace(spec.clips[0], source_id="ghost")

        assert "clip_source" in codes(replace(spec, clips=(clip, *spec.clips[1:])), Severity.ERROR)

    def test_a_duck_on_a_clip_that_is_not_in_the_mix_is_an_error(self) -> None:
        spec = f.spec_of()
        duck = replace(spec.ducking[0], clip_ids=("ghost",))

        assert "duck_target" in codes(replace(spec, ducking=(duck,)), Severity.ERROR)

    def test_automation_out_of_time_order_is_an_error(self) -> None:
        spec = f.spec_of()
        a = Automation(
            "auto:1", "clip", "a:aV", (GainPoint(500, 0.0), GainPoint(100, -3.0)),
            Interpolation.LINEAR, Provenance.HUMAN, "x",
        )  # fmt: skip

        assert "automation_order" in codes(replace(spec, automation=(a,)), Severity.ERROR)

    def test_overlapping_clips_on_one_track_are_a_warning_not_an_error(self) -> None:
        spec = f.spec_of()
        a, b = spec.clip("a:aV"), spec.clip("a:aW")
        assert a is not None and b is not None
        moved = replace(b, start_sample=a.end_sample - 100)
        clips = tuple(
            moved if c.clip_id == "a:aW" else replace(c, fade_in=c.fade_in) for c in spec.clips
        )

        found = codes(replace(spec, crossfades=(), clips=clips))

        assert "clip_overlap" in found and "clip_overlap" not in codes(
            replace(spec, crossfades=spec.crossfades, clips=clips), Severity.ERROR
        )

    def test_fades_longer_than_the_clip_are_a_warning(self) -> None:
        spec = f.spec_of()
        sfx = spec.clip("a:sfx:sfx:1")
        assert sfx is not None
        long = replace(sfx, fade_in=sfx.fade_out.__class__(5, 1))

        found = codes(
            replace(spec, clips=tuple(long if c.clip_id == sfx.clip_id else c for c in spec.clips))
        )

        assert "fades_overlap" in found


class TestBackendCapabilities:
    def test_what_the_mix_needs_depends_on_what_it_uses(self) -> None:
        spec = f.spec_of()

        ops = required_operations(spec)

        assert {
            "tempo",
            "fade",
            "envelope",
            "compressor",
            "limiter",
            "eq.highpass",
            "eq.peaking",
        } <= ops
        assert "pan" not in ops and ops <= ALL_OPERATIONS

    def test_a_missing_operation_is_an_error_that_names_it(self) -> None:
        spec = f.spec_of()

        issues = check_capabilities(spec, required_operations(spec) - {"limiter", "tempo"})

        assert {i.element_id for i in issues} == {"limiter", "tempo"}
        assert all(
            i.severity is Severity.ERROR and i.code == "operation_unsupported" for i in issues
        )

    def test_a_backend_that_can_do_everything_is_clean(self) -> None:
        assert check_capabilities(f.spec_of(), ALL_OPERATIONS) == ()


CHECKSUMS = {"rec1": "c-rec1", "song1": "c-song", "whoosh1": "c-whoosh"}


def hashes(spec: AudioMixSpecification, checksums: dict[str, str | None] | None = None,
           trims: dict[str, float] | None = None) -> dict[str, str]:  # fmt: skip
    return {
        s.bus_id: stem_hash(spec, s.bus_id, checksums or CHECKSUMS, trims or {}, "ffmpeg r1")
        for s in spec.stems
    }


class TestWhatMustBeRenderedAgain:
    def test_the_same_inputs_give_the_same_hashes(self) -> None:
        assert hashes(f.spec_of()) == hashes(f.spec_of())

    def test_a_dialogue_gain_re_renders_the_dialogue_only(self) -> None:
        before = hashes(f.spec_of())
        spec = f.spec_of()
        clip = spec.clip("a:aW")
        assert clip is not None
        changed = replace(
            spec,
            clips=tuple(replace(c, gain_db=c.gain_db + 3) if c is clip else c for c in spec.clips),
        )

        after = hashes(changed)

        assert after["dialogue"] != before["dialogue"]
        assert after["music"] == before["music"] and after["sfx"] == before["sfx"]

    def test_a_music_bus_change_leaves_the_dialogue_stem_alone(self) -> None:
        before = hashes(f.spec_of())
        spec = f.spec_of()
        music = spec.bus("music")
        assert music is not None
        changed = replace(
            spec,
            buses=tuple(replace(b, gain_db=b.gain_db - 2) if b is music else b for b in spec.buses),
        )

        after = hashes(changed)

        assert after["music"] != before["music"] and after["dialogue"] == before["dialogue"]

    def test_ducking_re_renders_the_music_it_acts_on_and_nothing_else(self) -> None:
        before = hashes(f.spec_of())
        spec = f.spec_of()
        duck = replace(spec.ducking[0], depth_db=spec.ducking[0].depth_db + 4)

        after = hashes(replace(spec, ducking=(duck,)))

        assert after["music"] != before["music"]
        assert after["dialogue"] == before["dialogue"] and after["sfx"] == before["sfx"]

    def test_a_replaced_source_re_renders_only_what_plays_it(self) -> None:
        before = hashes(f.spec_of())

        after = hashes(f.spec_of(), {**CHECKSUMS, "song1": "new"})

        assert after["music"] != before["music"]
        assert after["dialogue"] == before["dialogue"] and after["sfx"] == before["sfx"]

    def test_a_dialogue_level_trim_changes_that_stem_only(self) -> None:
        spec = f.spec_of()
        before = hashes(spec)

        after = hashes(spec, trims={"a:aV": 2.0})

        assert after["dialogue"] != before["dialogue"] and after["music"] == before["music"]

    def test_the_backend_is_part_of_every_hash(self) -> None:
        spec = f.spec_of()

        assert stem_hash(spec, "music", CHECKSUMS, {}, "ffmpeg r1") != stem_hash(
            spec, "music", CHECKSUMS, {}, "ffmpeg r2"
        )

    def test_the_mix_changes_with_a_stem_and_with_the_master_and_the_delivery(self) -> None:
        spec = f.spec_of()
        stems = hashes(spec)
        base = mix_hash(spec, stems, "b")

        assert mix_hash(spec, {**stems, "music": "x"}, "b") != base
        louder = replace(spec, loudness=replace(spec.loudness, integrated_lufs=-10.0))
        assert mix_hash(louder, stems, "b") != base
        assert mix_hash(spec, stems, "b") == base

    def test_a_new_loudness_target_leaves_every_stem_alone(self) -> None:
        spec = f.spec_of()
        louder = replace(spec, loudness=replace(spec.loudness, integrated_lufs=-10.0))

        assert hashes(louder) == hashes(spec)


class TestLevels:
    def test_dialogue_moves_only_part_of_the_way_to_the_target(self) -> None:
        trims = dialogue_trims({"a": -28.0}, target_lufs=-18.0, levelling=0.7, max_trim_db=6.0)

        assert trims == {"a": 6.0}  # 10 dB * 0.7 = 7, limited to 6

    def test_a_clip_near_the_target_is_left_alone_and_an_unmeasured_one_too(self) -> None:
        trims = dialogue_trims(
            {"a": -18.2, "b": None}, target_lufs=-18.0, levelling=0.7, max_trim_db=6.0
        )

        assert trims == {}

    def test_a_loud_clip_is_lowered_gently(self) -> None:
        assert dialogue_trims({"a": -10.0}, target_lufs=-18.0, levelling=0.5, max_trim_db=6.0) == {
            "a": -4.0
        }

    LOUDNESS = Loudness("youtube", -14.0, 1.0, None, -1.0)

    def test_inside_the_tolerance_nothing_is_corrected(self) -> None:
        assert loudness_correction(-14.6, self.LOUDNESS) is None
        assert loudness_correction(None, self.LOUDNESS) is None

    def test_outside_it_the_trim_is_the_difference_but_bounded(self) -> None:
        assert loudness_correction(-17.0, self.LOUDNESS) == 3.0
        assert loudness_correction(-40.0, self.LOUDNESS) == 9.0
        assert loudness_correction(0.0, self.LOUDNESS) == -12.0


def measurement(**kw: object) -> Measurement:
    base = {
        "integrated_lufs": -14.0, "loudness_range_lu": 6.0, "true_peak_dbtp": -1.5,
        "sample_peak_dbfs": -2.0, "duration_samples": 384000, "sample_rate": 48000,
        "channels": 2, "codec": "pcm_s24le",
    }  # fmt: skip
    base.update(kw)
    return Measurement(**base)  # type: ignore[arg-type]


def facts(**kw: object) -> OutputFacts:
    base: dict[str, object] = {
        "mix": measurement(), "stems": {"stem:dialogue": measurement(codec="pcm_f32le")},
        "silences": (), "dialogue_lufs": -16.0, "music_lufs": -30.0, "recombination_db": -200.0,
    }  # fmt: skip
    base.update(kw)
    return OutputFacts(**base)  # type: ignore[arg-type]


def found(**kw: object) -> dict[str, OutputIssue]:
    return {i.code: i for i in validate_output(f.spec_of(), facts(**kw))}


class TestJudgingTheMix:
    def test_a_good_mix_has_no_error(self) -> None:
        issues = validate_output(f.spec_of(), facts())

        assert not [i for i in issues if i.severity in {Severity.ERROR, Severity.CRITICAL}]

    def test_loudness_outside_the_tolerance_is_a_confirmed_error(self) -> None:
        issue = found(mix=measurement(integrated_lufs=-18.0))["loudness_out_of_range"]

        assert issue.severity is Severity.ERROR and issue.certainty is Certainty.CONFIRMED

    def test_a_silent_mix_is_critical(self) -> None:
        assert (
            found(mix=measurement(integrated_lufs=None))["silent_mix"].severity is Severity.CRITICAL
        )

    def test_a_true_peak_over_the_ceiling_is_an_error(self) -> None:
        assert "true_peak_over" in found(mix=measurement(true_peak_dbtp=0.5))

    def test_clipping_is_an_error_and_over_full_scale_is_critical(self) -> None:
        assert found(mix=measurement(sample_peak_dbfs=-0.05))["clipping"].severity is Severity.ERROR
        assert (
            found(mix=measurement(sample_peak_dbfs=0.4))["clipping"].severity is Severity.CRITICAL
        )

    def test_a_wide_loudness_range_is_only_a_likely_problem_and_only_when_the_delivery_limits_it(
        self,
    ) -> None:
        spec = f.spec_of()
        limited = replace(spec, loudness=replace(spec.loudness, max_loudness_range_lu=8.0))

        issues = {
            i.code: i
            for i in validate_output(limited, facts(mix=measurement(loudness_range_lu=12.0)))
        }

        assert issues["loudness_range"].certainty is Certainty.LIKELY
        assert "loudness_range" not in found(mix=measurement(loudness_range_lu=12.0))

    def test_the_wrong_format_is_an_error(self) -> None:
        assert "format" in found(mix=measurement(sample_rate=44100))
        assert "format" in found(mix=measurement(channels=1))
        assert "format" in found(mix=measurement(codec="pcm_s16le"))

    def test_a_mix_of_the_wrong_length_is_an_error(self) -> None:
        assert "duration_mismatch" in found(mix=measurement(duration_samples=300000))

    def test_a_stem_that_is_not_aligned_is_an_error_naming_it(self) -> None:
        issue = found(stems={"stem:music": measurement(duration_samples=380000)})[
            "stem_not_aligned"
        ]

        assert issue.element_id == "stem:music"

    def test_music_close_to_the_dialogue_under_speech_is_a_likely_masking_problem(self) -> None:
        issue = found(dialogue_lufs=-16.0, music_lufs=-20.0)["music_masks_dialogue"]

        assert issue.certainty is Certainty.LIKELY and issue.severity is Severity.WARNING
        assert "music_masks_dialogue" not in found(dialogue_lufs=-16.0, music_lufs=-30.0)

    def test_silence_where_sound_was_planned_is_a_warning_but_silence_elsewhere_is_intentional(
        self,
    ) -> None:
        planned = found(silences=((1.0, 3.0),))
        elsewhere = found(silences=((10.0, 12.0),))

        assert planned["silence_over_planned_sound"].severity is Severity.WARNING
        quiet = elsewhere["silence_where_nothing_planned"]
        assert quiet.severity is Severity.INFO and quiet.certainty is Certainty.INTENTIONAL

    def test_stems_that_do_not_recombine_to_the_mix_are_an_error(self) -> None:
        assert "recombination" in found(recombination_db=-20.0)
        assert "recombination_not_checked" in found(recombination_db=None)

    @pytest.mark.parametrize("peak", [-2.0, -1.0])
    def test_a_peak_below_full_scale_is_not_clipping(self, peak: float) -> None:
        assert "clipping" not in found(mix=measurement(sample_peak_dbfs=peak))
