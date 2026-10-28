"""The use case against fake owners and REAL upstream results: content analysis, audio timeline
and story plan are built by the owning modules' own code; only storage and catalogs are doubles."""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from media_house.modules.audio_intelligence.application.contracts import AudioIntelligenceTimeline
from media_house.modules.content_intelligence.application.contracts import ContentAnalysis
from media_house.modules.editing_intelligence.application.plan_edit import (
    Acquire,
    Advisors,
    EditResult,
    PlanEdit,
    PlanEditCommand,
)
from media_house.modules.editing_intelligence.application.results import EditResults
from media_house.modules.editing_intelligence.domain.errors import (
    InvalidEditConfig,
    InvalidStoryInput,
    MissingStoryPlan,
    NothingToEdit,
)
from media_house.modules.editing_intelligence.domain.model import ManualOverride
from media_house.modules.editing_intelligence.domain.profile import EditConfig, TargetDuration
from media_house.modules.editing_intelligence.domain.values import (
    Action,
    DurationMode,
    InputHow,
    Origin,
    Severity,
    TrackKind,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.modules.pacing_intelligence.domain.model import PacingAnalysis
from media_house.modules.story_architecture.application.contracts import (
    StoryConfig,
    StoryPlan,
    StoryResult,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import (
    Err,
    NotFoundError,
    Ok,
    OperationCancelledError,
    ValidationError,
)
from media_house.shared.filesystem import AppPaths
from tests.support.analysis_fakes import timeline
from tests.support.asset_dto import make_asset
from tests.support.ci_texts import lines_to_transcript
from tests.support.edit_fakes import decision
from tests.support.fakes import FixedClock
from tests.support.pace_fakes import T, speak
from tests.support.pace_fakes import analyse as analyse_pacing_of
from tests.support.story_fakes import (
    FakeAlignmentCatalog,
    FakeContentCatalog,
    StoryLibrary,
    plan_of,
)

CTX = JobContext.detached()


def duck(owner: object) -> Any:
    """A test double standing in for a port (it implements only what the use case calls)."""
    return owner


LINES = (
    "Today we look at the new phone and its screen.",
    "The screen is bright and very sharp outdoors.",
    "So yeah okay.",
    "The battery easily lasts two full days of use.",
    "Heat is a problem when charging fast.",
    "Overall I would recommend this phone to most people.",
)


def audio_for(lines: Sequence[str]) -> AudioIntelligenceTimeline:
    """An audio timeline whose words are exactly those the content analysis was built from."""
    transcript = lines_to_transcript(lines, asset_id="a1", pauses={4: 2.0})
    groups = [[(w.text, w.start, w.end) for w in seg.words] for seg in transcript.segments]
    return timeline(groups)


def analysis(lines: Sequence[str]) -> ContentAnalysis:
    return analysis_of_pauses(lines)


def analysis_of_pauses(lines: Sequence[str]) -> ContentAnalysis:
    from tests.support.ci_texts import analysis_of

    return analysis_of(lines_to_transcript(lines, asset_id="a1", pauses={4: 2.0}))


class FakeStories:
    def __init__(self, plans: dict[str, StoryPlan] | None = None) -> None:
        self.plans = dict(plans or {})

    def history(self, asset_id: str) -> list[MediaAssetDto]:
        return [make_asset(pid, display_name="story") for pid in self.plans]

    def load(self, plan_asset_id: str) -> Ok[StoryPlan] | Err[NotFoundError]:
        plan = self.plans.get(plan_asset_id)
        return Ok(plan) if plan is not None else Err(NotFoundError(plan_asset_id))


class FakePlanner:
    def __init__(self, plan: StoryPlan | None = None) -> None:
        self.plan = plan
        self.commands: list[object] = []

    def execute(self, command: Any, ctx: JobContext) -> Ok[StoryResult] | Err[ValidationError]:
        self.commands.append(command)
        if self.plan is None:
            return Err(ValidationError("no story"))
        return Ok(StoryResult(self.plan, make_asset("story-made"), created=True))


class Catalog:
    """A catalog of stored results by asset id that counts how often it is asked."""

    def __init__(self, stored: dict[str, object] | None = None) -> None:
        self.stored = dict(stored or {})
        self.calls = 0

    def latest(
        self, asset_id: str, *args: object, **kwargs: object
    ) -> Ok[tuple[Any, MediaAssetDto] | None]:
        self.calls += 1
        found = self.stored.get(asset_id)
        return Ok(None if found is None else (found, make_asset(f"doc-{asset_id}")))


class Inspections:
    def __init__(self) -> None:
        self.calls = 0

    def find(self, asset_id: str, config: object = None) -> None:
        self.calls += 1
        return


class Rig:
    def __init__(
        self,
        tmp_path: Path,
        *,
        lines: Sequence[str] = LINES,
        story: bool = True,
        fmt: str = "talking_head",
    ) -> None:
        self.library = StoryLibrary(tmp_path / "docs", ("a1",))
        (tmp_path / "docs").mkdir()
        self.content_analysis = analysis(lines)
        self.audio_timeline = audio_for(lines)
        self.story = plan_of(facts_for_analysis(self.content_analysis), StoryConfig(fmt))
        self.stories = FakeStories({"story-1": self.story} if story else {})
        self.planner = FakePlanner(self.story)
        self.content = FakeContentCatalog({"a1": self.content_analysis})
        self.audio = Catalog({"a1": self.audio_timeline})
        self.video = Catalog()
        self.alignments = FakeAlignmentCatalog()
        self.inspections = Inspections()
        self.edit = PlanEdit(
            duck(self.library),
            duck(self.planner),
            duck(self.stories),
            duck(self.content),
            duck(self.audio),
            duck(self.video),
            duck(self.alignments),
            duck(self.inspections),
            AppPaths.under_root(tmp_path / "app"),
            FixedClock(),
        )

    def run(self, **kw: Any) -> Any:
        command = PlanEditCommand(("a1",), **kw)
        return self.edit.execute(command, CTX)

    def plan(self, **kw: Any) -> EditResult:
        result = self.run(**kw)
        assert isinstance(result, Ok), result
        value: EditResult = result.value
        return value


def facts_for_analysis(analysis: ContentAnalysis) -> Any:
    from media_house.modules.story_architecture.application.views import content_facts

    return content_facts(analysis, "a1", "clip a1")


# --- the happy path ---------------------------------------------------------------------------
def test_a_stored_story_and_stored_analyses_become_a_stored_edit_plan(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    result = rig.plan(config=EditConfig(profile="talking_head"))

    plan = result.plan
    assert result.created and plan.plan_version == 1 and plan.supersedes is None
    assert plan.story.story_plan_id == rig.story.story_plan_id and plan.story.stored_as == "story-1"
    assert plan.track(TrackKind.AUDIO) and plan.decisions
    kinds = {(i.kind, i.how) for i in plan.inputs}
    assert ("story_plan", InputHow.REUSED) in kinds and (
        "content_analysis",
        InputHow.REUSED,
    ) in kinds
    assert ("audio_analysis", InputHow.REUSED) in kinds and (
        "video_analysis",
        InputHow.NOT_AVAILABLE,
    ) in kinds
    assert not plan.issues_of(Severity.ERROR)
    stored = EditResults(duck(rig.library)).latest("a1")
    assert isinstance(stored, Ok) and stored.value is not None and stored.value[0] == plan


def test_real_audio_gives_word_level_cut_points_and_the_long_pause_is_shortened(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)

    plan = rig.plan(config=EditConfig(profile="talking_head")).plan

    assert any(
        d.trims for d in plan.decisions
    )  # the 2 s pause before "Heat is a problem" is trimmed
    assert any(
        c.cut_in and c.cut_in.confidence and c.cut_in.confidence > 0.8
        for c in plan.track(TrackKind.AUDIO)
    )
    sound = plan.track(TrackKind.AUDIO)
    assert all(c.source.asset_id == "a1" for c in sound)


def test_every_decision_is_traceable_to_the_story_and_the_recording(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    plan = rig.plan().plan

    sections = {s.section_id for s in rig.story.sections}
    for each in plan.decisions:
        assert each.evidence
        if each.section_id is not None:
            assert each.section_id in sections
    assert all(c.section_id in sections for c in plan.track(TrackKind.AUDIO) if c.section_id)


def test_the_same_request_is_served_from_the_library_without_new_work(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    first, second = rig.plan(), rig.plan()

    assert first.created and not second.created
    assert second.plan == first.plan and second.asset.id == first.asset.id
    assert rig.library.registered == 1


def test_a_new_profile_makes_a_new_plan_that_follows_the_old_one(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    first = rig.plan(config=EditConfig(profile="talking_head"))
    second = rig.plan(config=EditConfig(profile="talking_head", target=TargetDuration(high=20.0)))
    third = rig.plan(config=EditConfig(profile="documentary"))

    assert (second.plan.plan_version, second.plan.supersedes) == (2, first.asset.id)
    assert (third.plan.plan_version, third.plan.supersedes) == (
        1,
        None,
    )  # another profile, its own history
    history = EditResults(duck(rig.library)).history("a1")
    assert len(history) == 3 and first.asset.id in {h.id for h in history}


def test_changed_evidence_makes_a_new_plan_and_unchanged_evidence_does_not(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    first = rig.plan()

    rig.audio.stored["a1"] = audio_for(LINES[:5])  # a different analysis of the recording
    changed = rig.plan()
    again = rig.plan()

    assert changed.created and changed.plan.plan_version == first.plan.plan_version + 1
    assert not again.created and again.asset.id == changed.asset.id


def test_a_changed_story_invalidates_the_plan_even_under_the_same_id(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    first = rig.plan()

    newer = plan_of(facts_for_analysis(rig.content_analysis), StoryConfig("tutorial"))
    assert newer.story_plan_id == rig.story.story_plan_id  # the test double keeps the id constant
    rig.stories.plans = {"story-2": newer}
    second = rig.plan()
    rebuilt = plan_of(facts_for_analysis(rig.content_analysis), StoryConfig("talking_head"))
    rig.stories.plans = {"story-3": rebuilt}
    third = rig.plan()

    assert second.created and second.plan.target_format == "tutorial"
    assert not third.created and third.asset.id == first.asset.id  # an identical story is a hit


# --- a person's decisions ---------------------------------------------------------------------
def test_a_manual_decision_is_carried_through_a_replan_unless_the_caller_replaces_it(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    cut = ManualOverride(decision_id="dec:0:u000002", action=Action.CUT, note="boring")
    first = rig.plan(overrides=(cut,))
    target = next(d for d in first.plan.decisions if d.origin is Origin.MANUAL)

    replanned = rig.plan(
        config=EditConfig(profile="talking_head", target=TargetDuration(high=60.0))
    )
    kept = rig.plan(
        overrides=(ManualOverride(decision_id="dec:0:u000002", action=Action.KEEP),),
        config=EditConfig(profile="talking_head", target=TargetDuration(high=60.0)),
    )
    dropped = rig.plan(
        carry_overrides=False,
        config=EditConfig(profile="talking_head", target=TargetDuration(high=70.0)),
    )

    assert target.action is Action.CUT
    assert (
        decision(replanned.plan, "dec:0:u000002").origin is Origin.MANUAL
        and decision(replanned.plan, "dec:0:u000002").action is Action.CUT
    )
    assert replanned.plan.overrides == first.plan.overrides
    assert decision(kept.plan, "dec:0:u000002").action is Action.KEEP
    assert decision(dropped.plan, "dec:0:u000002").origin is Origin.AUTOMATIC


def test_only_keep_and_cut_are_accepted_as_manual_decisions(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    result = rig.run(overrides=(ManualOverride(decision_id="dec:0:u000002", action=Action.COVER),))

    assert isinstance(result, Err) and isinstance(result.error, ValidationError)
    assert rig.library.registered == 0


# --- the story is a precondition --------------------------------------------------------------
def test_no_story_plan_is_an_error_not_a_silent_fallback(tmp_path: Path) -> None:
    rig = Rig(tmp_path, story=False)

    result = rig.run()

    assert isinstance(result, Err) and isinstance(result.error, MissingStoryPlan)
    assert rig.planner.commands == [] and rig.library.registered == 0


def test_a_missing_story_is_planned_by_its_owner_only_when_asked(tmp_path: Path) -> None:
    rig = Rig(tmp_path, story=False)

    made = rig.plan(acquire=Acquire.REQUEST, story_format="talking_head")

    assert len(rig.planner.commands) == 1
    assert made.plan.story.stored_as == "story-made"
    assert any(i.kind == "story_plan" and i.how is InputHow.REQUESTED for i in made.plan.inputs)


def test_a_story_plan_that_does_not_validate_is_refused(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    broken = replace(
        rig.story, beats=(replace(rig.story.beats[0], section_id="ghost"), *rig.story.beats[1:])
    )
    rig.stories.plans = {"story-1": broken}

    result = rig.run()

    assert isinstance(result, Err) and isinstance(result.error, InvalidStoryInput)
    assert rig.library.registered == 0


def test_a_story_without_sections_has_nothing_to_edit(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.stories.plans = {"story-1": replace(rig.story, sections=(), beats=())}

    result = rig.run()

    assert isinstance(result, Err) and isinstance(result.error, NothingToEdit)


def test_an_unknown_profile_is_refused_before_any_work(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    result = rig.run(config=EditConfig(profile="vaporwave"))

    assert isinstance(result, Err) and isinstance(result.error, InvalidEditConfig)
    assert rig.content.stored and rig.audio.calls == 0


# --- reuse, not recomputation -----------------------------------------------------------------
def test_results_the_caller_holds_are_used_as_given_and_nothing_is_looked_up(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)

    result = rig.plan(
        story_plan=rig.story,
        content_analyses={"a1": rig.content_analysis},
        audio_timelines={"a1": rig.audio_timeline},
        use_video=False,
        use_alignment=False,
    )

    hows = {i.kind: i.how for i in result.plan.inputs}
    assert hows["story_plan"] is InputHow.SUPPLIED and hows["content_analysis"] is InputHow.SUPPLIED
    assert hows["audio_analysis"] is InputHow.SUPPLIED
    assert rig.audio.calls == 0 and rig.video.calls == 0
    assert result.plan.story.stored_as is None


def test_missing_optional_analyses_give_a_conservative_honest_plan(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.audio.stored.clear()

    plan = rig.plan().plan

    assert plan.placements
    assert any("no audio analysis" in i.detail for i in plan.issues)
    assert not any(d.trims for d in plan.decisions)
    assert plan.confidence.value < rig_confidence_with_audio(tmp_path)


def rig_confidence_with_audio(tmp_path: Path) -> float:
    other = tmp_path / "other"
    other.mkdir()
    return Rig(other).plan().plan.confidence.value


def test_no_media_is_touched_only_one_derived_document_is_stored(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    rig.plan()

    assert rig.library.registered == 1
    ((_, asset),) = rig.library.assets.items()
    assert (
        asset.metadata["processing_type"] == "edit_plan"
        and asset.derivation is not None
        and asset.derivation.operation == "edit_plan"
    )


def test_cancellation_stops_the_work(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    ctx = JobContext.detached()
    ctx.cancellation.cancel()

    with pytest.raises(OperationCancelledError):
        rig.edit.execute(PlanEditCommand(("a1",)), ctx)
    assert rig.library.registered == 0


def test_the_clock_port_is_the_only_source_of_time(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    assert rig.plan().plan.created_at == FixedClock().now()


def test_advisors_are_part_of_the_identity_of_the_plan(tmp_path: Path) -> None:
    class Ranker:
        def identity(self) -> str:
            return "ranker/1"

        def rank(self, query: Any) -> None:
            return None

    rig = Rig(tmp_path)
    plain = rig.plan()
    advised_edit = PlanEdit(
        duck(rig.library),
        duck(rig.planner),
        duck(rig.stories),
        duck(rig.content),
        duck(rig.audio),
        duck(rig.video),
        duck(rig.alignments),
        duck(rig.inspections),
        AppPaths.under_root(tmp_path / "app"),
        FixedClock(),
        Advisors(coverage=Ranker()),
    )

    advised = advised_edit.execute(PlanEditCommand(("a1",)), CTX)

    assert isinstance(advised, Ok) and advised.value.created
    assert advised.value.plan.provenance.advisors == (("coverage", "ranker/1"),)
    assert plain.plan.provenance.advisors == ()
    assert DurationMode.SOFT  # imported for the public contract


# --- the pacing analysis -------------------------------------------------------------------------
def dense_pacing(asset_id: str = "a1", tokens: int = 22) -> PacingAnalysis:
    sentence = T(" ".join(["token"] * tokens), density=0.85, requires=2)
    return analyse_pacing_of(speak([sentence], asset_id=asset_id))


def edit_with_pacing(rig: Rig, tmp_path: Path, catalog: Catalog) -> PlanEdit:
    return PlanEdit(
        duck(rig.library),
        duck(rig.planner),
        duck(rig.stories),
        duck(rig.content),
        duck(rig.audio),
        duck(rig.video),
        duck(rig.alignments),
        duck(rig.inspections),
        AppPaths.under_root(tmp_path / "app"),
        FixedClock(),
        pacing=duck(catalog),
    )


def test_a_stored_pacing_analysis_of_the_same_recordings_is_read_recorded_and_shown(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    catalog = Catalog({"a1": dense_pacing()})

    result = edit_with_pacing(rig, tmp_path, catalog).execute(PlanEditCommand(("a1",)), CTX)

    assert isinstance(result, Ok)
    plan = result.value.plan
    assert ("pacing_analysis", InputHow.REUSED) in {(i.kind, i.how) for i in plan.inputs}
    assert next(s for s in plan.stages if s.stage == "pacing").state.value == "ok"
    notes = [n for n in plan.pacing_notes if n.level == "pacing"]
    assert notes and "allow processing time" in notes[0].detail


def test_a_changed_pacing_analysis_makes_a_new_plan_and_an_unchanged_one_does_not(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    catalog = Catalog({"a1": dense_pacing()})
    editor = edit_with_pacing(rig, tmp_path, catalog)

    first = editor.execute(PlanEditCommand(("a1",)), CTX)
    again = editor.execute(PlanEditCommand(("a1",)), CTX)
    catalog.stored["a1"] = dense_pacing(tokens=30)
    changed = editor.execute(PlanEditCommand(("a1",)), CTX)

    assert isinstance(first, Ok) and isinstance(again, Ok) and isinstance(changed, Ok)
    assert first.value.created and not again.value.created and changed.value.created
    assert changed.value.plan.plan_version == first.value.plan.plan_version + 1


def test_a_pacing_analysis_of_other_recordings_is_ignored_and_says_so(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    catalog = Catalog({"a1": dense_pacing(asset_id="zzz")})

    result = edit_with_pacing(rig, tmp_path, catalog).execute(PlanEditCommand(("a1",)), CTX)

    assert isinstance(result, Ok)
    record = next(i for i in result.value.plan.inputs if i.kind == "pacing_analysis")
    assert record.how is InputHow.NOT_AVAILABLE and "other recordings" in record.detail
    assert (
        next(s for s in result.value.plan.stages if s.stage == "pacing").state.value
        == "not_available"
    )


def test_a_supplied_pacing_analysis_is_used_without_asking_the_catalog(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    catalog = Catalog({"a1": dense_pacing(tokens=40)})

    result = edit_with_pacing(rig, tmp_path, catalog).execute(
        PlanEditCommand(("a1",), pacing=dense_pacing()), CTX
    )

    assert isinstance(result, Ok) and catalog.calls == 0
    assert ("pacing_analysis", InputHow.SUPPLIED) in {
        (i.kind, i.how) for i in result.value.plan.inputs
    }


def test_pacing_can_be_switched_off_and_a_missing_catalog_is_not_an_error(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    catalog = Catalog({"a1": dense_pacing()})

    off = edit_with_pacing(rig, tmp_path, catalog).execute(
        PlanEditCommand(("a1",), use_pacing=False), CTX
    )

    assert isinstance(off, Ok) and catalog.calls == 0
    assert (
        next(i for i in off.value.plan.inputs if i.kind == "pacing_analysis").how
        is InputHow.NOT_AVAILABLE
    )
