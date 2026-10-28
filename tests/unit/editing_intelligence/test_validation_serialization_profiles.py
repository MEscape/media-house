"""The validation boundary, the stored form and the editing profiles."""

import json
from dataclasses import replace
from fractions import Fraction
from typing import Any

import pytest

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.domain.errors import (
    InvalidEditConfig,
    InvalidEditDocument,
)
from media_house.modules.editing_intelligence.domain.facts import StoryFacts
from media_house.modules.editing_intelligence.domain.model import (
    EditPlan,
    Issue,
)
from media_house.modules.editing_intelligence.domain.profile import (
    DEFAULT_PROFILE,
    PROFILES,
    EditConfig,
    EditingProfile,
    TargetDuration,
)
from media_house.modules.editing_intelligence.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.editing_intelligence.domain.timing import to_rational
from media_house.modules.editing_intelligence.domain.validation import Validation, validate
from media_house.modules.editing_intelligence.domain.values import (
    Action,
    CheckState,
    DurationMode,
    IssueKind,
    Origin,
    Severity,
    TrackKind,
)
from tests.support.edit_fakes import (
    Recording,
    S,
    Sec,
    edit,
    story,
    talk,
    visual,
    with_video,
)

SENTENCES = [
    S("welcome to the review of this phone"),
    S("this battery lasts two full days of use", gap=0.4),
    S("overall I like it a lot", gap=2.5),
]
SECTIONS = [Sec("a", (0,), "essential"), Sec("b", (1,), "essential"), Sec("c", (2,), "essential")]


def build(with_cover: bool = True) -> tuple[Recording, StoryFacts, EditPlan]:
    rec = with_video(talk(SENTENCES, duration=50.0), [(0.0, 12.0), (20.0, 26.0)], speaking=[0])
    base = story(rec, SECTIONS)
    visuals = (visual(base, rec, 1, [("shot_1", "described", 1)]),) if with_cover else ()
    st = story(rec, SECTIONS, visuals=visuals, key_info=[("essential", 1, ())])
    return rec, st, edit(st, rec, EditConfig(profile="talking_head"))


def errors_of(plan: EditPlan, st: StoryFacts, rec: Recording) -> tuple[Validation, list[Issue]]:
    ids = iter(f"x{i}" for i in range(1000))
    result = validate(plan, st, {"a1": rec.evidence}, plan.duration.target, lambda: next(ids))
    return result, [i for i in result.issues if i.severity is Severity.ERROR]


def swap(plan: EditPlan, placement_id: str, **changes: Any) -> EditPlan:
    return replace(
        plan,
        placements=tuple(
            replace(p, **changes) if p.placement_id == placement_id else p for p in plan.placements
        ),
    )


# --- validation -------------------------------------------------------------------------------
def test_a_good_plan_has_no_errors_and_says_what_it_could_not_check() -> None:
    rec, st, plan = build()

    _, errors = errors_of(plan, st, rec)

    assert errors == []
    states = {c.name: c.state for c in plan.checks}
    assert states["source_ranges"] is CheckState.PASSED
    assert states["audio_gaps_and_silence"] is CheckState.NOT_CHECKED
    assert states["rendered_sync"] is CheckState.NOT_CHECKED
    assert states["black_frames"] in (CheckState.PASSED, CheckState.NOT_CHECKED)
    assert all(c.detail for c in plan.checks if c.state is CheckState.NOT_CHECKED)


def test_a_range_past_the_end_of_the_recording_is_an_error() -> None:
    rec, st, plan = build()
    clip = plan.track(TrackKind.AUDIO)[0]
    bad = replace(clip.source, end=Rational(60, 1))

    _, errors = errors_of(swap(plan, clip.placement_id, source=bad), st, rec)

    assert any(
        e.kind is IssueKind.INVALID_RANGE and "after the recording ends" in e.detail for e in errors
    )


def test_a_clip_of_a_recording_that_is_not_part_of_the_edit_is_an_error() -> None:
    rec, st, plan = build()
    clip = plan.track(TrackKind.AUDIO)[0]

    _, errors = errors_of(
        swap(plan, clip.placement_id, source=replace(clip.source, asset_id="zzz")), st, rec
    )

    assert any(e.kind is IssueKind.UNKNOWN_REFERENCE and "zzz" in e.detail for e in errors)


def test_overlapping_clips_without_a_bridge_are_an_error_and_so_is_an_accidental_gap() -> None:
    rec, st, plan = build()
    second = plan.track(TrackKind.AUDIO)[1]

    early = replace(
        second.timeline_start,
        numerator=second.timeline_start.numerator - 1 * second.timeline_start.denominator,
    )
    _, overlap = errors_of(swap(plan, second.placement_id, timeline_start=early), st, rec)
    late = to_rational(
        Fraction(second.timeline_start.numerator, second.timeline_start.denominator) + 3
    )
    _, gap = errors_of(swap(plan, second.placement_id, timeline_start=late), st, rec)

    assert any(e.kind is IssueKind.OVERLAP for e in overlap)
    assert any(e.kind is IssueKind.GAP for e in gap)


def test_an_estimate_that_disagrees_with_the_timeline_is_an_error() -> None:
    rec, st, plan = build()
    wrong = replace(plan, duration=replace(plan.duration, estimated=Rational(1, 1)))

    _, errors = errors_of(wrong, st, rec)

    assert any(e.kind is IssueKind.DURATION_INCONSISTENT for e in errors)


def test_dropping_essential_information_is_an_error() -> None:
    rec, st, plan = build()
    decisions = tuple(
        replace(d, action=Action.CUT) if d.decision_id == "dec:0:u001" else d
        for d in plan.decisions
    )

    _, errors = errors_of(replace(plan, decisions=decisions), st, rec)

    assert any(e.kind is IssueKind.ESSENTIAL_MISSING for e in errors)


def test_the_same_removal_by_a_person_is_a_warning() -> None:
    rec, st, plan = build()
    decisions = tuple(
        replace(d, action=Action.CUT, origin=Origin.MANUAL) if d.decision_id == "dec:0:u001" else d
        for d in plan.decisions
    )

    result, errors = errors_of(replace(plan, decisions=decisions), st, rec)

    assert errors == []
    assert any(
        i.kind is IssueKind.MANUAL_OVERRIDE_RISK and i.severity is Severity.WARNING
        for i in result.issues
    )


def test_a_decision_without_evidence_and_a_reference_to_nothing_are_errors() -> None:
    rec, st, plan = build()
    bare = tuple(
        replace(d, evidence=()) if d.decision_id == "dec:0:u000" else d for d in plan.decisions
    )
    ghost = tuple(
        replace(d, section_id="nope") if d.decision_id == "dec:0:u002" else d
        for d in plan.decisions
    )

    _, no_evidence = errors_of(replace(plan, decisions=bare), st, rec)
    _, unknown = errors_of(replace(plan, decisions=ghost), st, rec)

    assert any("without evidence" in e.detail for e in no_evidence)
    assert any(e.kind is IssueKind.UNKNOWN_REFERENCE and "nope" in e.detail for e in unknown)


def test_a_concept_the_story_never_named_is_unsupported_content() -> None:
    rec, st, plan = build()
    invented = tuple(replace(r, concept="a dragon") for r in plan.coverage)

    _, errors = errors_of(replace(plan, coverage=invented), st, rec)

    assert any(e.kind is IssueKind.UNSUPPORTED_CONTENT for e in errors)


def test_a_bridge_whose_sound_change_is_outside_its_cover_is_an_error() -> None:
    rec, st, plan = build()
    assert plan.audio_bridges
    moved = tuple(replace(b, audio_changes_at=Rational(999, 1)) for b in plan.audio_bridges)

    _, errors = errors_of(replace(plan, audio_bridges=moved), st, rec)

    assert any("outside the clip" in e.detail for e in errors)


def test_picture_and_sound_that_drifted_apart_are_an_error() -> None:
    rec, st, plan = build()
    picture = plan.track(TrackKind.VIDEO)[0]
    other = replace(
        picture.source,
        end=Rational(
            picture.source.end.numerator + picture.source.end.denominator,
            picture.source.end.denominator,
        ),
    )

    _, errors = errors_of(swap(plan, picture.placement_id, source=other), st, rec)

    assert any("not the same source range" in e.detail for e in errors)


def test_a_plan_with_no_video_analysis_never_reports_black_frames_as_checked() -> None:
    rec = talk(SENTENCES)
    plan = edit(story(rec, SECTIONS), rec)

    black = plan.check("black_frames")
    assert black is not None and black.state is CheckState.NOT_CHECKED


# --- stored form ------------------------------------------------------------------------------
def test_a_plan_survives_its_stored_form_unchanged() -> None:
    _, _, plan = build()

    assert plan.coverage and plan.audio_bridges and plan.decisions_for(Action.SHORTEN)
    assert plan_from_json(plan_to_json(plan)) == plan


def test_times_are_stored_exactly_as_numerator_and_denominator_never_as_floats() -> None:
    _, _, plan = build()

    document = json.loads(plan_to_json(plan))

    start = document["placements"][0]["timeline_start"]
    assert set(start) == {"numerator", "denominator"}
    assert document["document_type"] == "edit_plan"
    assert isinstance(document["placements"][0]["source"]["start"]["numerator"], int)


def test_the_reader_refuses_anything_it_does_not_know() -> None:
    _, _, plan = build()
    document = json.loads(plan_to_json(plan))

    def broken(**changes: object) -> str:
        return json.dumps({**document, **changes})

    with pytest.raises(InvalidEditDocument, match="not valid JSON"):
        plan_from_json("{")
    with pytest.raises(InvalidEditDocument, match="not an edit plan"):
        plan_from_json(broken(document_type="story_plan"))
    with pytest.raises(InvalidEditDocument, match="schema version"):
        plan_from_json(broken(schema_version=99))
    with pytest.raises(InvalidEditDocument, match="unknown fields"):
        plan_from_json(broken(surprise=1))
    with pytest.raises(InvalidEditDocument, match="unknown PlanState"):
        plan_from_json(broken(state="great"))
    missing = {k: v for k, v in document.items() if k != "profile"}
    with pytest.raises(InvalidEditDocument, match="missing field profile"):
        plan_from_json(json.dumps(missing))


# --- profiles ---------------------------------------------------------------------------------
def test_every_profile_is_valid_and_the_story_format_picks_its_namesake() -> None:
    assert all(p.problems() == [] for p in PROFILES.values())
    for name in ("talking_head", "tutorial", "documentary", "short", "top_n", "unboxing", "review"):
        assert EditConfig().resolve(name).name == name
    assert EditConfig().resolve("something_unknown").name == DEFAULT_PROFILE
    assert {"montage", "interview", "podcast", "product_introduction"} <= set(PROFILES)


def test_profiles_change_preferences_not_the_hard_constraints() -> None:
    assert not PROFILES["interview"].cut_fillers and PROFILES["talking_head"].cut_fillers
    assert PROFILES["documentary"].pause_max > PROFILES["short"].pause_max
    assert PROFILES["tutorial"].static_seconds > PROFILES["short"].static_seconds
    assert "demonstration" in PROFILES["tutorial"].protected_functions
    assert not hasattr(
        EditingProfile, "protect_key_information"
    )  # a hard constraint, not a preference


def test_an_unknown_or_inconsistent_configuration_is_refused_with_a_reason() -> None:
    with pytest.raises(InvalidEditConfig, match="Unknown profile"):
        EditConfig(profile="vaporwave").resolve("generic")
    bad = replace(PROFILES["generic"], pause_keep=5.0, pause_max=1.0)
    with pytest.raises(InvalidEditConfig, match="pause_keep"):
        EditConfig(profile=bad).resolve("generic")
    with pytest.raises(InvalidEditConfig, match="low must not exceed high"):
        EditConfig(target=TargetDuration(low=10, high=5)).resolve("generic")


def test_a_profile_built_for_one_run_changes_the_plan_and_the_fingerprint() -> None:
    strict = replace(PROFILES["generic"], name="strict", pause_max=0.5, pause_keep=0.2)
    rec = talk([S("the screen is bright and sharp"), S("it also gets very hot", gap=0.8)])
    st = story(rec, [Sec("a", (0, 1), "essential")])

    default = edit(st, rec)
    custom = edit(st, rec, EditConfig(profile=strict))

    assert len(default.track(TrackKind.AUDIO)) == 1 and len(custom.track(TrackKind.AUDIO)) == 2
    assert EditConfig(profile=strict).fingerprint(strict) != EditConfig().fingerprint(
        PROFILES["generic"]
    )


def test_a_target_with_mode_none_is_no_target() -> None:
    rec = talk([S("the screen is bright and sharp")])
    plan = edit(
        story(rec, [Sec("a", (0,), "essential")]),
        rec,
        EditConfig(target=TargetDuration(high=1.0, mode=DurationMode.NONE)),
    )

    assert plan.duration.target is None and plan.duration.met is None
