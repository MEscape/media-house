"""The revision loop end to end through the real composition root: raw clip -> built timeline ->
review -> quality report -> a person's words -> a plan -> a person's decision -> the change carried
out by the owner (the real audio mix engine, the real timeline builder) -> checked on the next
report -> rolled back.

No mock of any owner: what is verified is what the owners really stored.
"""

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.audio_mix_engine.application.contracts import (
    AudioMixer,
    MixCatalog,
    RenderMixCommand,
)
from media_house.modules.media_pipeline.application.contracts import (
    LAYER4_STAGES,
    LAYER5_STAGES,
    MediaPipeline,
    RunPipelineCommand,
    StageSettings,
)
from media_house.modules.music_and_sound_design.application.contracts import (
    ImportSoundLibraryCommand,
    SoundLibraryImporter,
)
from media_house.modules.quality_control.application.contracts import (
    AnalyzeQualityCommand,
    QualityAnalyzer,
)
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    OpenSessionCommand,
    ReviewCatalog,
    ReviewSession,
    ReviewSessions,
    ReviewState,
    Verdict,
)
from media_house.modules.revision_engine.application.contracts import (
    ChangeKind,
    ChangeStatus,
    DecideRevisionCommand,
    ExecuteRevisionCommand,
    PlanRevisionCommand,
    PlanRollbackCommand,
    PlanStatus,
    ProposalDecision,
    RevisionDecider,
    RevisionExecutorService,
    RevisionPlan,
    RevisionPlanner,
    RollbackPlanner,
)
from media_house.modules.revision_engine.application.mix_state import current_overrides
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimeline,
    BuiltTimelineCatalog,
    EditKind,
    HumanEdit,
    RecordEditsCommand,
    TimelineEditor,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Err, Ok
from tests.integration.media_pipeline.test_layer4_pipeline import no_speech_clip
from tests.integration.music_and_sound_design.conftest import build_library

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg"),
]
SETTINGS = StageSettings(output_format="9:16", layer4_render=False)
STAGES = LAYER4_STAGES | LAYER5_STAGES
CTX = JobContext.detached()
ADA = Actor.human("Ada")


@dataclass
class World:
    app: Application
    anchor: str
    session_id: str

    def get[T](self, protocol: type[T]) -> T:
        return self.app.container.resolve(protocol)

    @property
    def session(self) -> ReviewSession:
        found = self.get(ReviewCatalog).get(self.session_id)
        assert isinstance(found, Ok)
        return found.value

    @property
    def timeline(self) -> BuiltTimeline:
        found = self.get(BuiltTimelineCatalog).latest(self.anchor)
        assert isinstance(found, Ok) and found.value is not None
        return found.value[0]

    def overrides(self) -> tuple[str, ...]:
        return current_overrides(self.get(MixCatalog), self.anchor, self.timeline.timeline_id)

    def quality(self) -> None:
        done = self.get(QualityAnalyzer).execute(
            AnalyzeQualityCommand(self.anchor, review_session_id=self.session_id), CTX
        )
        assert isinstance(done, Ok), done

    def plan(self, *feedback: str, **kw: object) -> RevisionPlan:
        made = self.get(RevisionPlanner).execute(
            PlanRevisionCommand(self.session_id, author="Ada", feedback=feedback, **kw),  # type: ignore[arg-type]
            CTX,
        )
        assert isinstance(made, Ok), made
        return made.value.plan

    def approve(self, plan: RevisionPlan, actor: Actor = ADA) -> object:
        ids = tuple(
            ProposalDecision(c.proposal_id, Verdict.APPROVED)
            for c in plan.proposed_changes
            if c.status is not ChangeStatus.CONFLICT
        )
        return self.get(RevisionDecider).execute(
            DecideRevisionCommand(
                self.session_id, plan.revision_plan_id, actor, self.session.timeline, ids
            )
        )

    def execute(self, plan: RevisionPlan, actor: Actor = ADA):  # type: ignore[no-untyped-def]
        return self.get(RevisionExecutorService).execute(
            ExecuteRevisionCommand(self.session_id, plan.revision_plan_id, actor), CTX
        )


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    app = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    try:
        library = build_library(tmp_path / "sounds")
        imported = app.container.resolve(SoundLibraryImporter).execute(
            ImportSoundLibraryCommand(library.manifest), CTX
        )
        assert isinstance(imported, Ok)
        clip = no_speech_clip(tmp_path / "meme.mp4")
        ran = app.container.resolve(MediaPipeline).execute(
            RunPipelineCommand(paths=[clip], stages=STAGES, settings=SETTINGS), CTX
        )
        assert isinstance(ran, Ok)
        anchor = ran.value.assets[0].asset_id
        mixed = app.container.resolve(AudioMixer).execute(
            RenderMixCommand(anchor_asset_id=anchor), CTX
        )
        assert isinstance(mixed, Ok)
        opened = app.container.resolve(ReviewSessions).open(OpenSessionCommand(anchor, "Ada"))
        assert isinstance(opened, Ok)
        w = World(app, anchor, opened.value.session.session_id)
        w.quality()
        yield w
    finally:
        app.shutdown()


def test_a_persons_words_become_a_plan_that_waits_for_their_decision(world: World) -> None:
    plan = world.plan("Make the music quieter.")

    change = next(c for c in plan.proposed_changes if c.kind is ChangeKind.MIX_BUS_GAIN)
    assert change.param("bus") == "music" and change.param("delta_db") == "-3.0"
    assert change.owner_module == "audio_mix_engine" and change.status is ChangeStatus.PROPOSED
    assert plan.instructions[0].original == "Make the music quieter."
    assert world.session.state is ReviewState.AWAITING_REVISION_APPROVAL
    assert world.session.pending_feedback() == ()  # taken up by the plan


def test_nothing_is_carried_out_without_an_approval_by_a_person(world: World) -> None:
    plan = world.plan("Make the music quieter.")
    before = world.overrides()

    unapproved = world.execute(plan)
    by_the_system = world.approve(plan, Actor.system("someone"))

    assert isinstance(unapproved, Err)
    assert isinstance(by_the_system, Err)
    assert (
        world.overrides() == before
        and world.session.state is ReviewState.AWAITING_REVISION_APPROVAL
    )


def test_an_approved_change_is_carried_out_by_the_owner_and_checked_afterwards(
    world: World,
) -> None:
    plan = world.plan("Make the music quieter.")
    version_before = world.timeline.build.build_version
    assert isinstance(world.approve(plan), Ok)

    done = world.execute(plan)

    assert isinstance(done, Ok), done
    result = done.value.plan
    assert result.execution_status is PlanStatus.EXECUTED
    assert "bus:music=-3" in ",".join(world.overrides())  # recorded by the mix engine itself
    assert result.execution is not None and result.execution.checkpoint_id
    checkpoint = world.session.checkpoint(result.execution.checkpoint_id)
    assert (
        checkpoint is not None
        and checkpoint.verified
        and checkpoint.timeline.version == version_before
    )
    assert result.validation_results is not None  # checked on the next report, not assumed
    assert world.session.state in (ReviewState.REVIEW_READY, ReviewState.REVIEW_AGAIN_REQUIRED)
    music = next(c for c in result.proposed_changes if c.kind is ChangeKind.MIX_BUS_GAIN)
    assert music.status is ChangeStatus.EXECUTED and "mix rendered" in music.outcome


def test_something_rejected_is_not_proposed_again(world: World) -> None:
    plan = world.plan("Make the music quieter.")
    rejected = world.get(RevisionDecider).execute(
        DecideRevisionCommand(
            world.session_id,
            plan.revision_plan_id,
            ADA,
            world.session.timeline,
            tuple(
                ProposalDecision(c.proposal_id, Verdict.REJECTED, "I like it")
                for c in plan.proposed_changes
            ),
        )
    )
    assert isinstance(rejected, Ok) and world.session.state is ReviewState.REVISION_REJECTED
    world.get(ReviewSessions).advance(
        world.session_id, ReviewState.AWAITING_HUMAN_REVIEW, actor=ADA
    )

    again = world.plan("Make the music quieter.")

    assert not [c for c in again.proposed_changes if c.kind is ChangeKind.MIX_BUS_GAIN]


def test_a_section_the_person_locks_is_kept_out_of_every_later_plan(world: World) -> None:
    section = world.session.sections[0].section_id

    plan = world.plan("Leave this section exactly as it is.", section_id=section)

    assert section in world.session.locked_sections()
    assert all(
        section not in c.section_ids or c.status is ChangeStatus.CONFLICT
        for c in plan.proposed_changes
    )


def test_an_edit_made_after_the_approval_makes_the_plan_stale_and_nothing_runs(
    world: World,
) -> None:
    plan = world.plan("Make the music quieter.")
    assert isinstance(world.approve(plan), Ok)
    clip = next(c for c in world.timeline.video_clips() if c.kind.value == "primary")
    edited = world.get(TimelineEditor).execute(
        RecordEditsCommand(
            world.anchor,
            (HumanEdit(EditKind.MODIFY, clip.clip_id, (("opacity", "0.9"),), editor="Ada"),),
        ),
        CTX,
    )
    assert isinstance(edited, Ok)
    before = world.overrides()

    done = world.execute(plan)

    assert isinstance(done, Err)
    assert world.overrides() == before  # the plan did not run over the person's edit
    assert world.session.state is ReviewState.REVIEW_AGAIN_REQUIRED
    assert not world.session.current_approval(
        plan.revision_plan_id,
        __import__(
            "media_house.modules.review_orchestrator.application.contracts",
            fromlist=["ApprovalKind"],
        ).ApprovalKind.REVISION,
    )


def test_a_checkpoint_can_be_restored_as_a_revision_a_person_approves(world: World) -> None:
    plan = world.plan("Make the music quieter.")
    assert isinstance(world.approve(plan), Ok)
    done = world.execute(plan)
    assert isinstance(done, Ok) and done.value.plan.execution is not None
    checkpoint_id = done.value.plan.execution.checkpoint_id
    assert "bus:music=-3" in ",".join(world.overrides())
    if world.session.state is ReviewState.REVIEW_READY:
        world.get(ReviewSessions).advance(
            world.session_id, ReviewState.AWAITING_HUMAN_REVIEW, actor=ADA
        )

    proposed = world.get(RollbackPlanner).execute(
        PlanRollbackCommand(world.session_id, checkpoint_id, ADA)
    )
    assert isinstance(proposed, Ok), proposed
    rollback = proposed.value[0]
    assert rollback.proposed_changes[0].kind is ChangeKind.ROLLBACK
    assert rollback.approval_requirement.value == "C"  # it replaces decisions: only a person
    assert isinstance(world.approve(rollback), Ok)

    restored = world.execute(rollback)

    assert isinstance(restored, Ok), restored
    assert "bus:music=-3" not in ",".join(world.overrides())  # the mix of that time is back
