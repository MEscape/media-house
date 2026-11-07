"""Structure, edit, captions and graphics: what is wrong in the timeline itself."""

from dataclasses import replace
from fractions import Fraction

from media_house.modules.quality_control.domain import facts as f
from media_house.modules.quality_control.domain.checks_editing import check_edit
from media_house.modules.quality_control.domain.checks_structure import run_structure_checks
from media_house.modules.quality_control.domain.checks_text import check_captions, check_graphics
from media_house.modules.quality_control.domain.model import QualityIssue
from media_house.modules.quality_control.domain.values import Basis, Severity
from tests.unit.quality_control.fixtures import (
    GENERIC,
    PA,
    PB,
    TALKING,
    clean,
    codes,
    one,
    with_clip,
    with_sound,
)


def graphic_clip(facts: f.TimelineFacts) -> f.ClipFacts:
    found = facts.clip("o:graphic:g1")
    assert found is not None
    return found


def caption_clip(facts: f.TimelineFacts) -> f.ClipFacts:
    found = facts.clip("o:caption:cap:1")
    assert found is not None
    return found


def structure(facts: f.TimelineFacts, resolve: f.ResolveFacts | None = None) -> list[QualityIssue]:
    return run_structure_checks(facts, resolve, GENERIC)


# --- structure -------------------------------------------------------------------------------


def test_a_sound_timeline_has_no_structural_finding() -> None:
    assert structure(clean()) == []


def test_a_gap_between_main_shots_is_found_with_its_frames() -> None:
    facts = with_clip(clean(), PB, start_frame=110)

    issue = one(structure(facts), "structure.gap")

    assert (issue.start_frame, issue.end_frame) == (100, 110)
    assert issue.severity is Severity.HIGH and issue.basis is Basis.OBJECTIVE
    assert set(issue.affected_clips) == {PA, PB}


def test_two_clips_on_one_track_at_once_overlap() -> None:
    issue = one(structure(with_clip(clean(), PB, start_frame=90)), "structure.overlap")

    assert issue.start_frame == 90 and issue.affected_tracks == ("V1",)


def test_a_clip_beyond_the_end_is_found() -> None:
    issue = one(structure(with_clip(clean(), PB, end_frame=230)), "structure.beyond_timeline")

    assert (issue.start_frame, issue.end_frame) == (200, 230)


def test_missing_footage_is_critical_and_blocks_the_export() -> None:
    facts = clean()
    facts = replace(
        facts,
        sources=tuple(
            replace(s, available=False) if s.source_id == "rec1" else s for s in facts.sources
        ),
    )

    issue = one(structure(facts), "structure.media_missing")

    assert issue.severity is Severity.CRITICAL and issue.blocks_export
    assert PA in issue.affected_clips and "rec1" in issue.source_assets


def test_an_unresolved_main_clip_is_critical_but_an_unresolved_graphic_is_high() -> None:
    facts = with_clip(with_clip(clean(), PA, unresolved=True), "o:graphic:g1", unresolved=True)

    found = structure(facts)

    assert (
        one([i for i in found if PA in i.affected_clips], "structure.unresolved_clip").severity
        is Severity.CRITICAL
    )
    assert one(found, "graphic.missing_asset").severity is Severity.HIGH


def test_a_clip_stretched_on_one_axis_is_found_unless_stretch_is_the_fit() -> None:
    facts = with_clip(clean(), PA, scale_x=1.3)

    assert "structure.stretch" in codes(structure(facts))
    assert "structure.stretch" not in codes(
        structure(replace(facts, settings=replace(facts.settings, fit_mode="stretch")))
    )


def test_a_missing_sound_and_an_assumed_frame_rate_are_reported() -> None:
    facts = clean()
    facts = replace(facts, sounds=(), settings=replace(facts.settings, fps_assumed=True))

    assert {"structure.no_sound", "structure.frame_rate_assumed"} <= set(codes(structure(facts)))


def test_a_transition_that_cannot_run_is_reported_once_with_its_cut() -> None:
    facts = clean()
    facts = replace(
        facts,
        transitions=tuple(
            replace(t, supported=False, mode="unsupported") for t in facts.transitions
        ),
    )

    issue = one(structure(facts), "structure.transition_unsupported")

    assert issue.start_frame == 100


def test_the_builders_errors_are_carried_and_its_warnings_are_not_repeated() -> None:
    facts = replace(
        clean(),
        builder_issues=(
            f.BuilderIssueFacts("clip.range", "error", "range beyond the media", PB, 100, 200),
            f.BuilderIssueFacts("gap.main", "warning", "gap", None, None, None),
        ),
    )

    found = structure(facts)

    issue = one(found, "structure.builder_error")
    assert issue.origin == "timeline_builder" and issue.severity is Severity.CRITICAL
    assert not any(i.description == "gap" for i in found)


def test_what_the_specification_lists_and_the_timeline_lacks_is_found() -> None:
    facts = clean()
    listed = (*(c.clip_id for c in facts.clips), *(s.clip_id for s in facts.sounds), "v:lost")
    facts = replace(facts, specification_items=tuple(listed))

    issue = one(structure(facts), "structure.element_missing")

    assert "v:lost" in issue.description


def test_an_element_a_person_deleted_is_not_called_missing() -> None:
    facts = clean()
    listed = (*(c.clip_id for c in facts.clips), "v:lost")
    facts = replace(facts, specification_items=listed, deleted_by_human=("v:lost",))

    assert "structure.element_missing" not in codes(structure(facts))


def test_a_person_created_clip_is_not_unexpected() -> None:
    facts = with_clip(clean(), "o:graphic:g1", human=True)
    listed = tuple(c.clip_id for c in facts.clips if c.clip_id != "o:graphic:g1")
    everything = (
        *listed,
        *(s.clip_id for s in facts.sounds),
        *(t.transition_id for t in facts.transitions),
    )
    facts = replace(facts, specification_items=everything)

    assert "structure.element_unexpected" not in codes(structure(facts))


def test_resolve_holding_an_older_version_or_open_conflicts_is_reported() -> None:
    resolve = f.ResolveFacts(
        bound=True, bound_version=1, bound_hash_matches=False, open_conflicts=(PB,),
        unverified_elements=0, live_checked=True, live_changes=3, live_note="3 moved",
    )  # fmt: skip

    found = structure(clean(), resolve)

    assert {"resolve.stale_version", "resolve.conflict_open", "resolve.manual_changes"} <= set(
        codes(found)
    )
    assert one(found, "resolve.conflict_open").severity is Severity.HIGH


def test_a_timeline_not_in_resolve_has_nothing_to_compare() -> None:
    assert structure(clean(), None) == []


# --- the edit --------------------------------------------------------------------------------


def test_a_clip_of_two_frames_is_a_flash_frame() -> None:
    issue = one(check_edit(with_clip(clean(), PB, end_frame=102), GENERIC), "editing.flash_frame")

    assert issue.basis is Basis.OBJECTIVE and issue.severity is Severity.MEDIUM


def test_the_same_moment_shown_twice_is_repetition() -> None:
    facts = clean()
    pa = facts.clip(PA)
    assert pa is not None
    facts = with_clip(facts, PB, original_in=pa.original_in, original_out=pa.original_out)

    issue = one(check_edit(facts, GENERIC), "editing.repeated_footage")

    assert set(issue.affected_clips) == {PA, PB}


def test_a_cut_inside_one_take_is_a_jump_cut_unless_the_style_expects_it() -> None:
    facts = clean()
    pa = facts.clip(PA)
    assert pa is not None and pa.original_out is not None
    facts = with_clip(
        facts, PB, original_in=pa.original_out + Fraction(1, 2), original_out=pa.original_out + 4
    )

    issue = one(check_edit(facts, GENERIC), "editing.jump_cut")

    assert issue.basis is Basis.SUBJECTIVE and issue.severity is Severity.OPTIONAL
    assert "editing.jump_cut" not in codes(check_edit(facts, TALKING))


def test_a_subjective_finding_is_never_worse_than_medium() -> None:
    strict = replace(GENERIC, max_shot_seconds=1.0, min_shot_seconds=5.0)

    found = check_edit(clean(), strict)

    subjective = [i for i in found if i.basis is Basis.SUBJECTIVE]
    assert subjective and all(
        i.severity in (Severity.LOW, Severity.OPTIONAL, Severity.MEDIUM) for i in subjective
    )
    assert not any(i.blocks_export for i in subjective)


def test_a_long_opening_shot_with_nothing_on_top_is_a_weak_opening() -> None:
    quick = replace(GENERIC, opening_max_seconds=1.0)
    facts = replace(clean(), clips=tuple(c for c in clean().clips if c.kind == "primary"))

    assert "editing.weak_opening" in codes(check_edit(facts, quick))


def test_an_ending_with_no_fade_is_abrupt_and_a_fade_in_the_sound_fixes_it() -> None:
    facts = clean()
    bare = replace(facts, sounds=tuple(replace(s, fade_out=Fraction(0)) for s in facts.sounds))
    assert "editing.abrupt_ending" in codes(check_edit(bare, GENERIC))

    faded = with_sound(bare, "a:music:seg1", fade_out=Fraction(2))

    assert "editing.abrupt_ending" not in codes(check_edit(faded, GENERIC))


def test_a_long_stretch_with_nothing_on_top_is_a_static_stretch() -> None:
    only_main = replace(clean(), clips=tuple(c for c in clean().clips if c.kind == "primary"))
    tight = replace(GENERIC, variation_window_seconds=5.0)

    issue = one(check_edit(only_main, tight), "editing.static_stretch")

    assert (
        issue.severity is Severity.OPTIONAL
        and (issue.end_frame or 0) - (issue.start_frame or 0) >= 125
    )


# --- captions and graphics -------------------------------------------------------------------


def cue(index: int, start: int, end: int, text: str) -> f.CueFacts:
    return f.CueFacts(index, start, end, text)


def test_reading_speed_density_and_speed_of_captions_are_checked() -> None:
    text = "x" * 100
    facts = replace(clean(), cues=(cue(1, 0, 25, text), cue(2, 50, 60, "three little words")))

    found = check_captions(facts, GENERIC)

    assert {"caption.reading_speed", "caption.too_dense", "caption.too_fast"} <= set(codes(found))


def test_overlapping_empty_and_late_captions_are_found() -> None:
    facts = replace(
        clean(),
        cues=(
            cue(1, 0, 60, "one"),
            cue(2, 50, 100, "two"),
            cue(3, 120, 130, " "),
            cue(4, 190, 260, "late"),
        ),
    )

    found = codes(check_captions(facts, GENERIC))

    assert {"caption.overlap", "caption.empty", "caption.beyond_timeline"} <= set(found)


def test_dialogue_without_any_caption_is_a_suggestion_not_a_defect() -> None:
    facts = replace(clean(), cues=(), clips=tuple(c for c in clean().clips if c.kind != "caption"))

    issue = one(check_captions(facts, GENERIC), "caption.missing")

    assert issue.basis is Basis.SUBJECTIVE and not issue.blocks_export


def test_captions_in_two_styles_are_inconsistent() -> None:
    facts = with_clip(clean(), "o:caption:cap:1", design=(("font", "Anton"),))
    facts = with_clip(facts, "o:caption:cap:2", design=(("font", "Comic"),))
    facts = with_clip(facts, "o:caption:cap:1", design=(("font", "Anton"),))
    three = replace(
        facts,
        clips=(*facts.clips, replace(caption_clip(facts), clip_id="o:caption:cap:3")),
    )

    assert "caption.style_inconsistent" in codes(check_captions(three, GENERIC))


def test_a_missing_font_a_missing_brand_asset_and_an_unknown_zone_are_found() -> None:
    facts = replace(
        clean(),
        identity=replace(
            clean().identity, fonts=(("Anton", False),), brand_slots=(("logo", True, False),)
        ),
    )
    facts = with_clip(facts, "o:graphic:g1", zone="nowhere")

    found = codes(check_graphics(facts, GENERIC))

    assert {"graphic.font_missing", "graphic.brand_missing", "graphic.unknown_zone"} <= set(found)


def test_two_graphics_in_one_zone_at_once_overlap_and_off_frame_is_found() -> None:
    facts = clean()
    second = replace(graphic_clip(facts), clip_id="o:graphic:g2", start_frame=50, end_frame=90)
    facts = replace(facts, clips=(*facts.clips, second))
    facts = with_clip(facts, "o:graphic:g2", position_x=5000.0)

    found = codes(check_graphics(facts, GENERIC))

    assert "graphic.overlap" in found and "graphic.outside_frame" in found


def test_the_clean_timeline_has_no_caption_or_graphic_finding() -> None:
    facts = clean()

    assert check_captions(facts, GENERIC) == [] and check_graphics(facts, GENERIC) == []
