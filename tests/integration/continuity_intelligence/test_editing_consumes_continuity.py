"""The real editing stage consumes a stored continuity analysis.

Real: container, library, content analyzer, story planner, editing planner, derived-document
storage. Synthetic: the speech timeline and the continuity analysis itself (built by the pure
domain from hand-made evidence). This proves the plumbing: the stored analysis is found through
``ContinuityCatalog``, enters the plan's inputs and cache key, and changes the stage report. It says
nothing about how good the judgements are on real footage.
"""

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application
from media_house.modules.continuity_intelligence.application.contracts import (
    ContinuityCatalog,
    analysis_to_json,
)
from media_house.modules.continuity_intelligence.domain.values import RESULT_OPERATION
from media_house.modules.editing_intelligence.application.contracts import (
    EditPlanner,
    PlanEditCommand,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    MediaLibrary,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok
from media_house.shared.filesystem import AppPaths
from tests.integration.broll_intelligence.test_editing_consumes_broll import prepare
from tests.support.cont_fakes import jump_analysis

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="FFmpeg and ffprobe are required to import the test recordings",
    ),
]


def store(app: Application, talk: str) -> str:
    analysis = replace(jump_analysis(), sources=(talk,))
    library = app.container.resolve(MediaLibrary)
    source = library.get(talk)
    assert isinstance(source, Ok), source
    stored = DerivedDocuments(library, app.container.resolve(AppPaths)).store(
        source.value,
        analysis_to_json(analysis),
        filename="continuity_analysis.json",
        spec=DocumentSpec(RESULT_OPERATION, 1, {"case": "editing"}),
        display_name="continuity",
        metadata={"profile": analysis.profile, "edit_plan_id": analysis.edit_plan_id},
    )
    assert isinstance(stored, Ok), stored
    return stored.value.id


def test_the_editor_finds_the_stored_analysis_and_reports_it(
    app: Application, tmp_path: Path
) -> None:
    talk, _ = prepare(app, tmp_path)
    store(app, talk)
    planner = app.container.resolve(EditPlanner)

    planned = planner.execute(PlanEditCommand((talk,)), JobContext.detached())

    assert isinstance(planned, Ok), planned
    plan = planned.value.plan
    assert ("continuity_analysis", "reused") in {(i.kind, i.how.value) for i in plan.inputs}
    stage = next(s for s in plan.stages if s.stage == "continuity_analysis")
    assert stage.state.value == "ok"
    assert app.container.resolve(ContinuityCatalog).latest(talk) is not None


def test_without_a_stored_analysis_the_baseline_stands_and_a_new_one_is_a_new_plan_version(
    app: Application, tmp_path: Path
) -> None:
    talk, _ = prepare(app, tmp_path)
    planner = app.container.resolve(EditPlanner)
    ctx = JobContext.detached()
    before = planner.execute(PlanEditCommand((talk,)), ctx)
    assert isinstance(before, Ok)
    stage = next(s for s in before.value.plan.stages if s.stage == "continuity_analysis")
    assert stage.state.value == "not_available"

    store(app, talk)
    after = planner.execute(PlanEditCommand((talk,)), ctx)
    again = planner.execute(PlanEditCommand((talk,)), ctx)

    assert isinstance(after, Ok) and isinstance(again, Ok)
    assert after.value.created and after.value.plan.plan_version == 2
    assert after.value.plan.supersedes == before.value.asset.id
    assert not again.value.created  # the identical inputs hit the cache
