"""The real editing stage consumes a stored framing analysis.

Real: container, library, content analyzer, story planner, editing planner, derived-document
storage. Synthetic: the speech timeline and the framing analysis itself (built by the pure domain
from hand-made tracks). The test recording is a sound file, so the plan has no picture clip here:
this proves the plumbing (the stored analysis is found through ``ReframingCatalog`` for the wanted
target, enters the plan's inputs and cache key, changes the stage report). What a picture clip
does with a recommendation is proved by ``tests/unit/editing_intelligence/test_framing_input.py``.
It says nothing about how good the framing is on real footage.
"""

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application
from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.application.contracts import (
    EditPlan,
    EditPlanner,
    InputRecord,
    PlanEditCommand,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    MediaLibrary,
)
from media_house.modules.reframing_intelligence.application.contracts import (
    ReframingAnalysis,
    analysis_to_json,
)
from media_house.modules.reframing_intelligence.domain.geometry import TargetFormat
from media_house.modules.reframing_intelligence.domain.values import RESULT_OPERATION
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok
from media_house.shared.filesystem import AppPaths
from tests.integration.broll_intelligence.test_editing_consumes_broll import prepare
from tests.support.refr_fakes import T11, T916, analyse, shot, still

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="FFmpeg and ffprobe are required to import the test recordings",
    ),
]

PERSON = (0.40, 0.15, 0.60, 0.90)


def framing_for(talk: str, target: TargetFormat = T916) -> ReframingAnalysis:
    """A finished analysis whose one shot covers the whole recording."""
    found = analyse(shot("s0", 0, 60, (still("p", PERSON, end=60, step=0.5),)), target=target)
    rec = replace(found.recommendations[0], asset_id=talk, end=Rational(1000, 1))
    source = replace(found.sources[0], asset_id=talk)
    return replace(found, recommendations=(rec,), sources=(source,))


def store(app: Application, talk: str, analysis: ReframingAnalysis, case: str) -> str:
    library = app.container.resolve(MediaLibrary)
    source = library.get(talk)
    assert isinstance(source, Ok), source
    stored = DerivedDocuments(library, app.container.resolve(AppPaths)).store(
        source.value,
        analysis_to_json(analysis),
        filename="reframing_analysis.json",
        spec=DocumentSpec(RESULT_OPERATION, 1, {"case": case}),
        display_name="reframing",
        metadata={"target": analysis.target.name, "profile": analysis.profile},
    )
    assert isinstance(stored, Ok), stored
    return stored.value.id


def plan_of(app: Application, talk: str, **kw: object):  # type: ignore[no-untyped-def]
    planned = app.container.resolve(EditPlanner).execute(
        PlanEditCommand((talk,), **kw),  # type: ignore[arg-type]
        JobContext.detached(),
    )
    assert isinstance(planned, Ok), planned
    return planned.value


def framing_input(plan: EditPlan) -> InputRecord:
    return next(i for i in plan.inputs if i.kind == "framing_analysis")


def test_the_editor_finds_the_stored_analysis_and_names_exactly_which_one_it_read(
    app: Application, tmp_path: Path
) -> None:
    talk, _ = prepare(app, tmp_path)
    analysis = framing_for(talk)
    store(app, talk, analysis, "one")

    plan = plan_of(app, talk).plan

    read = framing_input(plan)
    assert read.how.value == "reused" and read.identity == analysis.reframing_analysis_id
    assert next(s for s in plan.stages if s.stage == "framing_analysis").state.value == "ok"


def test_the_wanted_target_picks_its_own_analysis(app: Application, tmp_path: Path) -> None:
    talk, _ = prepare(app, tmp_path)
    nine, one = framing_for(talk, T916), framing_for(talk, T11)
    store(app, talk, nine, "nine")
    store(app, talk, one, "one")  # the newer of the two

    for_nine = plan_of(app, talk, reframing_target="9:16").plan
    for_one = plan_of(app, talk, reframing_target="1:1").plan

    assert framing_input(for_nine).identity == nine.reframing_analysis_id
    assert framing_input(for_one).identity == one.reframing_analysis_id


def test_an_analysis_of_other_recordings_is_ignored_and_said_so(
    app: Application, tmp_path: Path
) -> None:
    talk, _ = prepare(app, tmp_path)
    stranger = framing_for("somebody-else")
    store(app, talk, stranger, "stranger")

    plan = plan_of(app, talk).plan

    read = framing_input(plan)
    assert read.how.value == "not_available" and "other recordings" in read.detail
    assert next(s for s in plan.stages if s.stage == "framing_analysis").state.value == (
        "not_available"
    )


def test_a_new_framing_analysis_is_a_new_plan_version_and_the_same_one_is_a_cache_hit(
    app: Application, tmp_path: Path
) -> None:
    talk, _ = prepare(app, tmp_path)
    before = plan_of(app, talk)
    stage = next(s for s in before.plan.stages if s.stage == "framing_analysis")
    assert stage.state.value == "not_available"

    store(app, talk, framing_for(talk), "one")
    after = plan_of(app, talk)
    again = plan_of(app, talk)

    assert after.created and after.plan.plan_version == 2
    assert after.plan.supersedes == before.asset.id
    assert not again.created  # the identical inputs hit the cache
