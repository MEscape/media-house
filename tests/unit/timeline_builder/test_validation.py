"""A specification that cannot be built is refused; what the builder cannot carry out is kept and
reported; the finished timeline is judged on its own, not on the compiler's word."""

from dataclasses import replace

from media_house.core.domain import Rational
from media_house.modules.timeline_builder.domain import facts as sf
from media_house.modules.timeline_builder.domain.model import ExternalCheck
from media_house.modules.timeline_builder.domain.preflight import IdentityExpectation
from media_house.modules.timeline_builder.domain.profile import BuilderConfig, Capabilities
from media_house.modules.timeline_builder.domain.values import (
    BuildStatus,
    CheckKind,
    CheckState,
    ClipState,
    Severity,
    TransitionMode,
)
from tests.unit.edit_plan_compiler import fixtures as cf
from tests.unit.timeline_builder import fixtures as f


def severities(t) -> dict[str, Severity]:  # type: ignore[no-untyped-def]
    return {i.code: i.severity for i in t.issues}


class TestRefusedBeforeBuilding:
    def test_a_schema_it_cannot_read_is_refused(self) -> None:
        facts = replace(f.facts_of(), schema_version=9)

        assert any("schema 9" in m for m in f.refused(facts))

    def test_a_vocabulary_it_does_not_know_is_refused(self) -> None:
        facts = f.facts_of()
        weird = replace(facts, compilation=replace(facts.compilation, vocabulary_version=7))

        assert any("vocabulary" in m for m in f.refused(weird))

    def test_an_unknown_word_is_refused_not_guessed(self) -> None:
        facts = f.change_tracks(f.facts_of(), "V3", role="holograms")

        assert any("unknown kind or role" in m for m in f.refused(facts))

    def test_footage_that_left_the_library_is_refused(self) -> None:
        facts = f.facts_of()

        problems = f.refused(facts, live=f.live_for(facts, missing=("rec1",)))

        assert any("rec1" in m and "library" in m for m in problems)

    def test_a_file_that_is_gone_from_disk_is_refused(self) -> None:
        facts = f.facts_of()

        problems = f.refused(facts, live=f.live_for(facts, gone=("song1",)))

        assert any("song1" in m for m in problems)

    def test_a_source_changed_after_compilation_is_refused(self) -> None:
        facts = f.facts_of()
        live = f.live_for(facts)
        changed = sf.LiveSources(
            found={**live.found, "rec1": replace(live.found["rec1"], checksum="other")}
        )

        assert any("changed after" in m for m in f.refused(facts, live=changed))

    def test_a_reference_to_an_unlisted_asset_is_refused(self) -> None:
        facts = f.change_video(f.facts_of(), "v:pB", asset_id="nowhere")

        assert any("nowhere" in m for m in f.refused(facts))

    def test_a_missing_critical_dependency_is_refused(self) -> None:
        facts = f.facts_of()
        broken = replace(
            facts,
            dependencies=tuple(
                replace(d, state="missing") if d.reference == "rec1" else d
                for d in facts.dependencies
            ),
        )

        assert any("rec1" in m and "missing" in m for m in f.refused(broken))

    def test_a_need_the_library_could_not_meet_is_reported_not_a_reason_to_refuse(self) -> None:
        facts = f.facts_of()
        unmet = replace(
            facts,
            dependencies=(
                *facts.dependencies,
                sf.DependencyFact(
                    dependency_id="miss:music:1",
                    kind="sound",
                    reference="miss:music:1",
                    state="missing",
                    detail="no music in the sound library",
                ),
            ),
        )

        t = f.built(unmet)  # no clip was placed for it, so nothing is half-built

        dep = next(d for d in t.dependencies if d.dependency_id == "miss:music:1")
        assert dep.state.value == "missing" and t.status is BuildStatus.PARTIAL
        assert dep in t.unresolved()
        # nothing relies on it and nobody can produce it from here: the manifest says it, the
        # compiler's own finding is carried, and it is not said a third time
        assert not any(i.element_id == "miss:music:1" for i in t.issues)

    def test_the_wrong_creator_identity_is_refused(self) -> None:
        facts = f.facts_of()

        assert any("identity" in m for m in f.refused(facts, expect=IdentityExpectation("other")))
        assert any(
            "version" in m
            for m in f.refused(facts, expect=IdentityExpectation("mesc", identity_version=9))
        )
        assert any(
            "another style" in m
            for m in f.refused(facts, expect=IdentityExpectation("mesc", style_fingerprint="x"))
        )

    def test_the_right_identity_passes(self) -> None:
        facts = f.facts_of()

        f.built(facts, expect=IdentityExpectation("mesc", 2, cf.STYLE_FP))

    def test_every_reason_is_reported_not_only_the_first(self) -> None:
        facts = replace(f.facts_of(), schema_version=9)

        problems = f.refused(facts, live=f.live_for(facts, missing=("rec1", "song1")))

        assert len(problems) >= 3

    def test_a_bad_configuration_is_refused(self) -> None:
        import pytest

        from media_house.modules.timeline_builder.domain.errors import InvalidBuilderConfig

        with pytest.raises(InvalidBuilderConfig):
            f.build(config=BuilderConfig(audio_channels="surround"))


class TestTimingOfTheBuiltTimeline:
    def test_overlapping_main_clips_are_refused(self) -> None:
        facts = f.change_video(f.facts_of(), "v:pB", start_frame=90, timeline_start=Rational(18, 5))

        assert any("overlap" in m for m in f.refused(facts))

    def test_a_clip_whose_source_does_not_fill_its_time_is_refused(self) -> None:
        facts = f.change_video(f.facts_of(), "v:pB", source_end=Rational(30, 1))

        assert any("does not fill" in m for m in f.refused(facts))

    def test_a_source_range_beyond_the_media_is_refused(self) -> None:
        facts = f.facts_of()
        short = f.change_asset(facts, "rec1", duration=Rational(22, 1))

        assert any("after the end" in m for m in f.refused(short))

    def test_a_clip_beyond_the_timeline_is_refused(self) -> None:
        facts = replace(f.facts_of(), timebase=replace(f.facts_of().timebase, duration_frames=150))

        assert any("after the timeline" in m for m in f.refused(facts))

    def test_frames_that_do_not_match_exact_times_are_refused(self) -> None:
        facts = f.change_video(f.facts_of(), "v:pB", start_frame=101)

        assert any("do not match" in m for m in f.refused(facts))

    def test_audio_out_of_sync_with_its_picture_is_refused(self) -> None:
        facts = f.change_audio(f.facts_of(), "a:aW", timeline_start=Rational(41, 10))

        assert any("out of sync" in m for m in f.refused(facts))

    def test_fades_longer_than_the_clip_are_refused(self) -> None:
        facts = f.change_audio(
            f.facts_of(), "a:music:seg1", fade_in=Rational(5, 1), fade_out=Rational(5, 1)
        )

        assert any("fade" in m for m in f.refused(facts))

    def test_samples_that_do_not_match_exact_times_are_refused(self) -> None:
        facts = f.change_audio(f.facts_of(), "a:sfx:sfx:1", start_sample=1)

        assert any("samples" in m for m in f.refused(facts))

    def test_a_gap_on_the_main_picture_is_reported_not_hidden(self) -> None:
        facts = f.facts_of()
        # pA ends at 3.6 s instead of 4 s; its sound follows; the dissolve is taken out
        shorter = f.change_video(
            facts,
            "v:pA",
            end_frame=90,
            timeline_end=Rational(18, 5),
            source_end=Rational(68, 5),
            transition_out=None,
        )
        shorter = f.change_video(shorter, "v:pB", transition_in=None)
        shorter = f.change_audio(
            shorter,
            "a:aV",
            timeline_end=Rational(18, 5),
            source_end=Rational(68, 5),
            end_sample=172_800,
        )
        shorter = replace(
            shorter,
            transitions=(),
            automation=tuple(a for a in shorter.automation if a.kind != "crossfade"),
        )

        t = f.built(shorter)

        issue = next(i for i in t.issues if i.code == "gap_on_main_picture")
        assert issue.severity is Severity.WARNING
        assert (issue.start_frame, issue.end_frame) == (90, 100)

    def test_a_tolerated_gap_is_not_reported(self) -> None:
        # the default fixture has no gap; the tolerance knob must still be accepted
        t = f.built(config=BuilderConfig(gap_tolerance_frames=3))

        assert "gap_on_main_picture" not in f.codes(t)


class TestUnsupportedOperations:
    def test_a_transition_outside_the_vocabulary_is_kept_and_never_replaced(self) -> None:
        facts = f.change_transition(f.facts_of(), "cut:pA>pB", kind="whip_pan", family="movement")

        t = f.built(facts)

        [tr] = t.transitions
        assert tr.mode is TransitionMode.UNSUPPORTED and not tr.supported
        assert tr.kind == "whip_pan"  # the look is not swapped for another
        assert severities(t)["unsupported_transition"] is Severity.UNSUPPORTED
        assert t.status is BuildStatus.PARTIAL

    def test_blocking_on_unsupported_stops_the_build(self) -> None:
        facts = f.change_transition(f.facts_of(), "cut:pA>pB", kind="whip_pan", family="movement")

        problems = f.refused(facts, config=BuilderConfig(block_on_unsupported=True))

        assert any("whip_pan" in m for m in problems)

    def test_the_target_application_can_narrow_the_vocabulary(self) -> None:
        caps = Capabilities(transitions=frozenset({"hard_cut"}))

        t = f.built(caps=caps)

        [tr] = t.transitions
        assert tr.mode is TransitionMode.DISSOLVE and not tr.supported
        assert "unsupported_transition" in f.codes(t)

    def test_too_many_tracks_for_the_target_application_is_reported(self) -> None:
        t = f.built(caps=Capabilities(max_video_tracks=3))

        assert "too_many_video_tracks" in f.codes(t)

    def test_insufficient_handles_in_the_files_are_found_by_the_builder_itself(self) -> None:
        facts = f.facts_of()
        # the specification claims enough handle after pA; the file pA plays holds 0.03 s more
        rec = facts.asset("rec1")
        assert rec is not None
        alt = replace(rec, asset_id="alt", original_asset_id="alt", duration=Rational(1403, 100))
        short = replace(facts, assets=(*facts.assets, alt))
        short = f.change_video(short, "v:pA", asset_id="alt", original_asset_id="alt")

        t = f.built(short)

        issue = next(i for i in t.issues if i.code == "insufficient_handles")
        assert issue.severity is Severity.UNSUPPORTED and issue.element_id == "cut:pA>pB"

    def test_a_handle_shortfall_the_specification_states_is_reported(self) -> None:
        facts = f.facts_of()
        stated = f.change_transition(
            facts,
            "cut:pA>pB",
            handles=replace(facts.transitions[0].handles, sufficient=False),
        )

        t = f.built(stated)

        assert severities(t)["insufficient_handles"] is Severity.UNSUPPORTED

    def test_a_native_effect_the_target_cannot_do_is_kept_and_not_applied(self) -> None:
        facts = f.facts_of(vfx=cf.vfx())
        native = replace(
            facts,
            effects=tuple(replace(e, execution="native") for e in facts.effects),
        )

        t = f.built(native, caps=Capabilities(effects=frozenset({"vignette"})))

        [effect] = f.where(t.video_clips(), lambda c: c.clip_id == "v:pA").effects
        assert not effect.applied and effect.effect_type == "slow_motion"
        assert severities(t)["unsupported_effect"] is Severity.UNSUPPORTED

    def test_a_native_effect_the_target_can_do_is_applied(self) -> None:
        facts = f.facts_of(vfx=cf.vfx())
        native = replace(
            facts, effects=tuple(replace(e, execution="native") for e in facts.effects)
        )

        t = f.built(native)

        [effect] = f.where(t.video_clips(), lambda c: c.clip_id == "v:pA").effects
        assert effect.applied
        assert "unsupported_effect" not in f.codes(t)


class TestUnresolvedDependencies:
    def test_a_graphic_without_its_file_is_kept_unresolved_and_reported(self) -> None:
        facts = f.facts_of()
        live = f.live_for(facts, gone=("gfx1",))

        t = f.built(facts, live=live)

        g = f.where(t.video_clips(), lambda c: c.clip_id == "o:graphic:g1")
        assert g.state is ClipState.UNRESOLVED
        assert severities(t)["unresolved_dependency"] is Severity.WARNING
        assert t.status is BuildStatus.PARTIAL
        assert any(d.reference == "gfx1" for d in t.unresolved())

    def test_blocking_on_unresolved_stops_the_build(self) -> None:
        facts = f.facts_of()

        problems = f.refused(
            facts,
            live=f.live_for(facts, gone=("gfx1",)),
            config=BuilderConfig(block_on_unresolved=True),
        )

        assert any("gfx1" in m or "g1" in m for m in problems)

    def test_a_requested_dependency_names_its_owner(self) -> None:
        facts = f.facts_of()
        requested = replace(
            facts,
            dependencies=(
                *facts.dependencies,
                sf.DependencyFact(
                    dependency_id="rendered:late",
                    kind="rendered_graphic",
                    reference="late",
                    state="requested",
                    owner="motion_graphics_engine",
                    request="graphics",
                ),
            ),
        )

        t = f.built(requested)

        dep = next(d for d in t.dependencies if d.dependency_id == "rendered:late")
        assert dep.owner == "motion_graphics_engine" and dep.request == "graphics"
        assert any("motion_graphics_engine" in i.message for i in t.issues)

    def test_the_dependency_manifest_lists_who_needs_each_file(self) -> None:
        t = f.built()

        dep = next(d for d in t.dependencies if d.reference == "rec1")
        assert {"v:pA", "v:pB"} <= set(dep.required_by)
        handle = next(d for d in t.dependencies if d.dependency_id == "handle:cut:pA>pB")
        assert handle.state.value == "resolved"


class TestMediaAndPictureFindings:
    def test_a_variable_frame_rate_source_is_flagged(self) -> None:
        facts = f.change_asset(f.facts_of(), "rec1", frame_rate_variable=True)

        assert severities(f.built(facts))["variable_frame_rate"] is Severity.WARNING

    def test_a_source_at_another_frame_rate_is_noted(self) -> None:
        facts = f.change_asset(f.facts_of(), "broll1", frame_rate=Rational(30, 1))

        assert severities(f.built(facts))["frame_rate_conversion"] is Severity.INFO

    def test_a_source_tagged_with_another_colour_is_flagged_and_nothing_is_converted(self) -> None:
        facts = f.change_asset(
            f.facts_of(),
            "rec1",
            color=sf.ColorFact(space="bt2020nc", primaries="bt2020", transfer="smpte2084"),
        )

        t = f.built(facts)

        assert severities(t)["color_mismatch"] is Severity.WARNING
        assert t.source("rec1").color_transfer == "smpte2084"  # type: ignore[union-attr]
        assert t.settings.color.transfer == "bt709"

    def test_a_source_of_another_shape_reports_the_fit_that_applies(self) -> None:
        facts = f.change_asset(f.facts_of(), "broll1", width=1080, height=1920)

        t = f.built(facts)

        issue = next(i for i in t.issues if i.code == "fit_applied")
        assert "contain" in issue.message and issue.element_id == "v:pC"

    def test_a_zone_the_identity_does_not_define_is_reported(self) -> None:
        facts = f.change_overlay(f.facts_of(), "o:graphic:g1", zone="nowhere")

        assert severities(f.built(facts))["unknown_zone"] is Severity.WARNING

    def test_a_design_choice_the_identity_does_not_list_is_reported(self) -> None:
        facts = f.facts_of()
        stripped = replace(facts, creator=replace(facts.creator, presets=()))

        t = f.built(stripped)

        assert severities(t)["identity_reference"] is Severity.WARNING

    def test_an_unavailable_font_and_unbound_brand_slot_are_reported(self) -> None:
        facts = f.facts_of()
        creator = replace(
            facts.creator,
            fonts=tuple(replace(x, available=False) for x in facts.creator.fonts),
            brand_assets=(sf.BrandAssetFact(slot="logo", required=True, asset_id=None),),
        )

        codes = f.codes(f.built(replace(facts, creator=creator)))

        assert {"font_unavailable", "brand_asset_unbound"} <= codes

    def test_picture_with_sound_in_its_source_but_no_audio_clip_is_reported(self) -> None:
        facts = f.facts_of()
        silent = replace(
            facts,
            audio=tuple(a for a in facts.audio if a.item_id != "a:aW"),
            automation=tuple(m for m in facts.automation if m.kind != "crossfade"),
        )
        silent = replace(
            silent,
            video=tuple(
                replace(v, transition_in=v.transition_in, transition_out=v.transition_out)
                for v in silent.video
            ),
        )

        t = f.built(silent)

        issue = next(i for i in t.issues if i.code == "missing_dialogue")
        assert issue.element_id == "v:pB"

    def test_findings_of_the_compiler_are_carried_not_lost(self) -> None:
        facts = f.facts_of(vfx=cf.vfx())

        t = f.built(facts)

        carried = [i for i in t.issues if i.origin == "specification"]
        assert any(i.code == "transition_not_executable" for i in carried)
        assert all(
            i.severity in {Severity.INFO, Severity.WARNING, Severity.REVIEW} for i in carried
        )


class TestChecksNeedingARender:
    def test_nothing_is_claimed_without_a_render(self) -> None:
        t = f.built()

        assert len(t.validation.not_run) == len(CheckKind)
        assert t.validation.external == ()

    def test_an_external_result_is_recorded_and_a_failure_is_a_finding(self) -> None:
        checks = (
            ExternalCheck(kind=CheckKind.BLACK_FRAMES, state=CheckState.FAILED, tool="ffmpeg",
                          detail="12 black frames", start_frame=40, end_frame=52),
            ExternalCheck(kind=CheckKind.AV_SYNC, state=CheckState.PASSED, tool="probe"),
        )  # fmt: skip

        t = f.built(checks=checks)

        assert CheckKind.BLACK_FRAMES not in t.validation.not_run
        assert CheckKind.AV_SYNC not in t.validation.not_run
        assert CheckKind.MISSING_AUDIO in t.validation.not_run
        issue = next(i for i in t.issues if i.code == "external.black_frames")
        assert issue.origin == "external" and (issue.start_frame, issue.end_frame) == (40, 52)

    def test_a_check_that_did_not_run_does_not_count_as_run(self) -> None:
        checks = (ExternalCheck(kind=CheckKind.BLACK_FRAMES, state=CheckState.NOT_RUN),)

        t = f.built(checks=checks)

        assert CheckKind.BLACK_FRAMES in t.validation.not_run
