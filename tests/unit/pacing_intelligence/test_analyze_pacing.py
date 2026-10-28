"""The use case against fake owners and REAL upstream results: content analysis, audio timeline and
story plan are built by the owning modules' own code; only storage and catalogs are doubles."""

from pathlib import Path
from typing import Any

import pytest

from media_house.modules.pacing_intelligence.application.analyze_pacing import (
    AnalyzePacing,
    AnalyzePacingCommand,
    PacingResult,
)
from media_house.modules.pacing_intelligence.application.results import PacingResults
from media_house.modules.pacing_intelligence.domain.errors import (
    InvalidPacingConfig,
    MissingEvidence,
)
from media_house.modules.pacing_intelligence.domain.profile import PacingConfig
from media_house.modules.pacing_intelligence.domain.values import (
    InputHow,
    PauseVerdict,
    RecommendationType,
    Severity,
    Strategy,
)
from media_house.modules.story_architecture.application.contracts import StoryConfig
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, OperationCancelledError, ValidationError
from media_house.shared.filesystem import AppPaths
from tests.support.fakes import FixedClock
from tests.support.story_fakes import (
    FakeAlignmentCatalog,
    FakeContentCatalog,
    StoryLibrary,
    plan_of,
)
from tests.unit.editing_intelligence.test_plan_edit import (
    LINES,
    Catalog,
    FakeStories,
    Inspections,
    analysis,
    audio_for,
    duck,
    facts_for_analysis,
)

CTX = JobContext.detached()
TALKING_HEAD = PacingConfig(profile="talking_head")


class Rig:
    def __init__(self, tmp_path: Path, *, story: bool = True, audio: bool = True) -> None:
        self.library = StoryLibrary(tmp_path / "docs", ("a1",))
        (tmp_path / "docs").mkdir()
        self.content_analysis = analysis(LINES)
        self.audio_timeline = audio_for(LINES)
        self.story = plan_of(facts_for_analysis(self.content_analysis), StoryConfig("talking_head"))
        self.stories = FakeStories({"story-1": self.story} if story else {})
        self.content = FakeContentCatalog({"a1": self.content_analysis})
        self.audio = Catalog({"a1": self.audio_timeline} if audio else {})
        self.video = Catalog()
        self.alignments = FakeAlignmentCatalog()
        self.inspections = Inspections()
        self.analyzer = AnalyzePacing(
            duck(self.library),
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
        return self.analyzer.execute(AnalyzePacingCommand(("a1",), **kw), CTX)

    def result(self, **kw: Any) -> PacingResult:
        found = self.run(**kw)
        assert isinstance(found, Ok), found
        value: PacingResult = found.value
        return value


def test_stored_results_become_a_stored_pacing_analysis_through_real_contracts(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)

    result = rig.result(config=TALKING_HEAD)

    analysis_ = result.analysis
    assert result.created and analysis_.analysis_version == 1 and analysis_.supersedes is None
    assert analysis_.story is not None and analysis_.story.stored_as == "story-1"
    kinds = {(i.kind, i.how) for i in analysis_.inputs}
    assert ("story_plan", InputHow.REUSED) in kinds and ("audio_analysis", InputHow.REUSED) in kinds
    assert ("content_analysis", InputHow.REUSED) in kinds
    assert ("video_analysis", InputHow.NOT_AVAILABLE) in kinds
    assert analysis_.passages and analysis_.sections and not analysis_.issues_of(Severity.ERROR)
    # the two seconds of silence before the fifth line are dead air for this profile
    assert any(p.verdict is PauseVerdict.DEAD_AIR for p in analysis_.pauses)
    assert analysis_.recommendations_of(RecommendationType.TIGHTEN_PAUSE)
    stored = PacingResults(duck(rig.library)).latest("a1", story_plan_id=rig.story.story_plan_id)
    assert isinstance(stored, Ok) and stored.value is not None and stored.value[0] == analysis_


def test_real_word_timing_and_content_density_reach_the_passages(tmp_path: Path) -> None:
    analysis_ = Rig(tmp_path).result(config=TALKING_HEAD).analysis

    assert all(p.speech_rate_wpm is not None for p in analysis_.passages if p.words >= 4)
    assert any(p.information_density.value is not None for p in analysis_.passages)
    assert analysis_.passages[0].span.precision.value == "word_aligned"
    assert analysis_.provenance.upstream  # what every figure rests on


def test_the_same_request_is_served_from_the_library_without_new_work(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    first, second = rig.result(), rig.result()

    assert first.created and not second.created
    assert second.analysis == first.analysis and second.asset.id == first.asset.id
    assert rig.library.registered == 1


def test_a_new_strategy_or_profile_makes_a_new_analysis_that_follows_the_old_one(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)

    first = rig.result(config=TALKING_HEAD)
    second = rig.result(config=PacingConfig(profile="talking_head", strategy=Strategy.CONCISE))
    other = rig.result(config=PacingConfig(profile="documentary"))

    assert (second.analysis.analysis_version, second.analysis.supersedes) == (2, first.asset.id)
    assert (other.analysis.analysis_version, other.analysis.supersedes) == (1, None)
    assert len(PacingResults(duck(rig.library)).history("a1")) == 3


def test_changed_evidence_invalidates_the_analysis_and_unchanged_evidence_does_not(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    first = rig.result()

    reworded = [LINES[0], LINES[1], "So yes okay.", *LINES[3:]]
    rig.content.stored["a1"] = analysis(reworded)
    changed = rig.result()
    again = rig.result()

    assert (
        changed.created and changed.analysis.analysis_version == first.analysis.analysis_version + 1
    )
    assert not again.created and again.asset.id == changed.asset.id


def test_a_changed_story_invalidates_the_analysis_and_an_identical_one_does_not(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    first = rig.result()

    rig.stories.plans = {
        "story-2": plan_of(facts_for_analysis(rig.content_analysis), StoryConfig("tutorial"))
    }
    second = rig.result()
    rig.stories.plans = {
        "story-3": plan_of(facts_for_analysis(rig.content_analysis), StoryConfig("talking_head"))
    }
    third = rig.result()

    assert second.created and second.analysis.story is not None
    assert (
        second.analysis.story.target_format == "tutorial" and second.analysis.profile == "tutorial"
    )
    assert not third.created and third.asset.id == first.asset.id


def test_the_profile_follows_the_story_format_only_when_none_is_given(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    default = rig.result().analysis
    chosen = rig.result(config=PacingConfig(profile="documentary")).analysis

    assert default.profile == "talking_head" and chosen.profile == "documentary"


def test_without_a_stored_story_the_analysis_is_made_and_says_narrative_is_unavailable(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, story=False)

    analysis_ = rig.result().analysis

    assert analysis_.story is None and analysis_.sections == ()
    assert any(i.kind.value == "narrative_limited" for i in analysis_.issues)
    assert analysis_.profile == "generic"  # nothing stronger is known, nothing is inferred
    assert analysis_.passages


def test_a_supplied_story_and_supplied_results_are_used_and_nothing_is_looked_up(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, story=False)

    result = rig.result(
        story_plan=rig.story,
        audio_timelines={"a1": rig.audio_timeline},
        content_analyses={"a1": rig.content_analysis},
        use_video=False,
        use_alignment=False,
    )

    hows = {i.kind: i.how for i in result.analysis.inputs}
    assert hows["story_plan"] is InputHow.SUPPLIED and hows["audio_analysis"] is InputHow.SUPPLIED
    assert rig.audio.calls == 0 and rig.video.calls == 0 and rig.content.stored


def test_with_no_speech_and_no_picture_there_is_nothing_to_analyse(tmp_path: Path) -> None:
    rig = Rig(tmp_path, audio=False)

    result = rig.run()

    assert isinstance(result, Err) and isinstance(result.error, MissingEvidence)
    assert rig.library.registered == 0


def test_bad_input_is_refused_before_any_work(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    nothing = rig.analyzer.execute(AnalyzePacingCommand(()), CTX)
    twice = rig.analyzer.execute(AnalyzePacingCommand(("a1", "a1")), CTX)
    profile = rig.run(config=PacingConfig(profile="vaporwave"))

    assert isinstance(nothing, Err) and isinstance(nothing.error, ValidationError)
    assert isinstance(twice, Err) and isinstance(twice.error, ValidationError)
    assert isinstance(profile, Err) and isinstance(profile.error, InvalidPacingConfig)
    assert rig.library.registered == 0 and rig.audio.calls == 0


def test_cancellation_stops_the_work_and_the_clock_port_supplies_the_time(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    assert rig.result().analysis.created_at == FixedClock().now()
    ctx = JobContext.detached()
    ctx.cancellation.cancel()

    (tmp_path / "second").mkdir()
    with pytest.raises(OperationCancelledError):
        Rig(tmp_path / "second").analyzer.execute(AnalyzePacingCommand(("a1",)), ctx)
