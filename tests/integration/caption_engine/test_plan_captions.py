"""Caption plans against a real Application and Media Library: stored, reused, versioned,
read through the catalog; the no-speech path; an unavailable renderer degrades honestly."""

from dataclasses import replace
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application
from media_house.modules.caption_engine.application.contracts import (
    DOCUMENT_TYPE,
    Action,
    CaptionCatalog,
    CaptionConfig,
    CaptionDecision,
    CaptionMode,
    CaptionPlanner,
    InvalidCaptionTimeline,
    PlanCaptionsCommand,
    RenderStatus,
    ResultState,
    StyleNotAvailable,
)
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok
from tests.integration.caption_engine.conftest import (
    caption_timeline,
    import_anchor,
    resolve_style,
    store_timeline,
)
from tests.support.caption_fakes import audio_timeline, said

pytestmark = pytest.mark.integration

SPEECH = "Heute gibt es die Batterie für nur 19,99 € bei Lidl. Das ist wirklich krass!"


def test_a_plan_is_stored_reused_and_versioned(app: Application, tmp_path: Path) -> None:
    anchor = import_anchor(app, tmp_path)
    store_timeline(app, anchor, audio_timeline(said(SPEECH), asset_id=anchor.id))
    style, ref = resolve_style(app, anchor.id, "shorts_haul")
    planner = app.container.resolve(CaptionPlanner)
    command = PlanCaptionsCommand(timeline=caption_timeline(anchor.id), style=ref)

    first = planner.execute(command, JobContext.detached())
    assert isinstance(first, Ok), first
    plan = first.value.plan
    assert first.value.created and plan.mode is CaptionMode.FULL_CAPTIONS
    assert plan.state is ResultState.OK and plan.plan_version == 1
    assert plan.style is not None and plan.style.fingerprint == style.fingerprint
    assert plan.provenance.profile == "shorts_haul"
    assert " ".join(s.text for s in plan.segments).split() == SPEECH.split()
    assert all(s.layout.zone == "caption_band" for s in plan.segments)
    assert all(s.render.status is RenderStatus.NOT_REQUESTED for s in plan.segments)
    assert any(r.kind == "transcript" and r.how.value == "reused" for r in plan.inputs)
    assert plan.subtitles is not None and "19,99 €" in plan.subtitles.vtt
    assert plan.transcripts[0].words_shown == len(SPEECH.split())

    again = planner.execute(command, JobContext.detached())
    assert isinstance(again, Ok) and not again.value.created
    assert again.value.asset.id == first.value.asset.id

    rejected = plan.segments[0].segment_id
    changed = planner.execute(
        PlanCaptionsCommand(
            timeline=caption_timeline(anchor.id),
            style=ref,
            decisions=(CaptionDecision(item_id=rejected, action=Action.REJECT),),
        ),
        JobContext.detached(),
    )
    assert isinstance(changed, Ok) and changed.value.created
    assert changed.value.plan.plan_version == 2
    assert changed.value.plan.supersedes == first.value.asset.id
    assert rejected not in {s.segment_id for s in changed.value.plan.segments}

    carried = planner.execute(command, JobContext.detached())  # decisions carry over
    assert isinstance(carried, Ok)
    assert rejected not in {s.segment_id for s in carried.value.plan.segments}

    latest = app.container.resolve(CaptionCatalog).latest(anchor.id, project_id="p1")
    assert isinstance(latest, Ok) and latest.value is not None
    assert latest.value[0] == carried.value.plan
    path = app.container.resolve(MediaLibrary).local_path(latest.value[1].id)
    assert isinstance(path, Ok)
    assert f'"document_type": "{DOCUMENT_TYPE}"' in path.value.read_text(encoding="utf-8")


def test_long_form_profile_from_the_style_burns_in_nothing(
    app: Application, tmp_path: Path
) -> None:
    anchor = import_anchor(app, tmp_path)
    store_timeline(app, anchor, audio_timeline(said(SPEECH), asset_id=anchor.id))
    _, ref = resolve_style(app, anchor.id, "ranking", 1920, 1080)

    result = app.container.resolve(CaptionPlanner).execute(
        PlanCaptionsCommand(
            timeline=caption_timeline(anchor.id, width=1920, height=1080), style=ref
        ),
        JobContext.detached(),
    )

    assert isinstance(result, Ok), result
    plan = result.value.plan
    assert plan.mode is CaptionMode.DISABLED and plan.segments == ()
    assert plan.provenance.profile == "ranking"
    assert plan.subtitles is not None and plan.subtitles.srt.startswith("1\n00:00:00,000 --> ")


def test_the_no_speech_path_stores_a_disabled_plan(app: Application, tmp_path: Path) -> None:
    anchor = import_anchor(app, tmp_path)
    _, ref = resolve_style(app, anchor.id, "shorts_meme")

    result = app.container.resolve(CaptionPlanner).execute(
        PlanCaptionsCommand(timeline=caption_timeline(anchor.id, edit=None), style=ref),
        JobContext.detached(),
    )

    assert isinstance(result, Ok), result
    plan = result.value.plan
    assert plan.mode is CaptionMode.DISABLED and plan.edit is None and plan.project_id is None
    assert [r.owner for r in plan.reprocess] == ["audio_intelligence"]
    assert any(r.kind == "transcript" and r.how.value == "not_available" for r in plan.inputs)


def test_an_unavailable_renderer_degrades_to_a_partial_plan(
    app: Application, tmp_path: Path
) -> None:
    anchor = import_anchor(app, tmp_path)
    store_timeline(app, anchor, audio_timeline(said(SPEECH), asset_id=anchor.id))
    _, ref = resolve_style(app, anchor.id, "shorts_haul")
    planner = app.container.resolve(CaptionPlanner)
    command = PlanCaptionsCommand(timeline=caption_timeline(anchor.id), style=ref, render=True)

    result = planner.execute(command, JobContext.detached())

    assert isinstance(result, Ok), result
    plan = result.value.plan
    assert plan.state is ResultState.PARTIAL
    assert {s.render.status for s in plan.segments} == {RenderStatus.NOT_AVAILABLE}
    assert any(st.stage == "render" and st.state.value == "not_available" for st in plan.stages)
    retried = planner.execute(command, JobContext.detached())
    assert isinstance(retried, Ok) and retried.value.created  # tried again, new version
    assert retried.value.plan.plan_version == 2


def test_bad_input_is_refused(app: Application, tmp_path: Path) -> None:
    anchor = import_anchor(app, tmp_path)
    style, _ = resolve_style(app, anchor.id, "shorts_haul")
    planner = app.container.resolve(CaptionPlanner)
    empty = caption_timeline(anchor.id)

    no_clips = planner.execute(
        PlanCaptionsCommand(timeline=replace(empty, clips=()), style=style),
        JobContext.detached(),
    )
    wrong_size = planner.execute(
        PlanCaptionsCommand(
            timeline=caption_timeline(anchor.id, width=1920, height=1080), style=style
        ),
        JobContext.detached(),
    )
    bad_config = planner.execute(
        PlanCaptionsCommand(
            timeline=empty, style=style, config=CaptionConfig(overrides={"nope.x": 1})
        ),
        JobContext.detached(),
    )

    assert isinstance(no_clips, Err) and isinstance(no_clips.error, InvalidCaptionTimeline)
    assert isinstance(wrong_size, Err) and isinstance(wrong_size.error, StyleNotAvailable)
    assert isinstance(bad_config, Err)
