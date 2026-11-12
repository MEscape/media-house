"""The thumbnail_design task: the owner's words, a reaction picture, the products of the run."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.tasks import TaskResult
from media_house.modules.media_pipeline.domain.plan import plan_asset
from media_house.modules.media_pipeline.domain.values import TaskId, TaskKind, TaskStatus
from media_house.modules.thumbnail_design.application.contracts import (
    DesignedThumbnails,
    DesignedVariant,
    DesignThumbnailsCommand,
)
from media_house.shared.errors import Ok
from tests.support.asset_dto import make_asset
from tests.unit.media_pipeline.test_executors import Recorder, executors, job
from tests.unit.media_pipeline.test_layer4_tasks import style_result

K = TaskKind
STYLE = (K.VISUAL_STYLE, TaskResult(TaskStatus.SUCCEEDED, payload=style_result(1920, 1080)))
SETTINGS = StageSettings(
    thumbnail_headline=("Erstmals", "online!"),
    product_images=(("Pure Impact", "pi"), ("Seven Kings", "sk"), ("Armageddon", "ar")),
    thumbnail_device_product="Pure Impact",
    thumbnail_device_price="79,99 €",
    thumbnail_badge="04:30",
    thumbnail_logo="lidl",
)


def designed(created: bool = True) -> Any:
    variants = tuple(
        DesignedVariant(name=n, label=n, asset=make_asset(f"t_{n}"), created=created, issues=())
        for n in ("a", "b")
    )
    return Ok(DesignedThumbnails(variants=variants, style_fingerprint="fp"))


class Reactions:
    def __init__(self, ids: tuple[str, ...]) -> None:
        self.ids = ids

    def list(self) -> tuple[Any, ...]:
        return tuple(SimpleNamespace(id=i) for i in self.ids)


def test_it_is_a_layer4_task_that_needs_a_picture() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested={K.THUMBNAIL_DESIGN})
    spec = {s.id.kind: s for s in plan.specs}[K.THUMBNAIL_DESIGN]
    assert spec.needs == {TaskId("a", K.VISUAL_STYLE), TaskId("a", K.INSPECT)}
    audio_only = plan_asset("a", has_video=False, has_audio=True, requested={K.THUMBNAIL_DESIGN})
    assert K.THUMBNAIL_DESIGN in audio_only.not_applicable


def test_without_a_headline_nothing_is_invented() -> None:
    designer = Recorder(designed())
    ex = executors(StageSettings(), thumbnail_designer=designer)
    result = ex.thumbnail_design(job(K.THUMBNAIL_DESIGN, STYLE))
    assert result.status is TaskStatus.SKIPPED_UNAVAILABLE
    assert designer.commands == []


def test_the_brief_comes_from_the_run_and_the_tagged_reaction() -> None:
    designer = Recorder(designed())
    ex = executors(SETTINGS, thumbnail_designer=designer, reactions=Reactions(("me", "old")))
    result = ex.thumbnail_design(job(K.THUMBNAIL_DESIGN, STYLE))

    assert result.status is TaskStatus.SUCCEEDED
    assert result.facts["rendered"] == ["t_a", "t_b"]
    (command,) = designer.commands
    assert isinstance(command, DesignThumbnailsCommand)
    brief = command.brief
    assert command.anchor_asset_id == "a"
    assert brief.reaction_asset_id == "me"
    assert brief.headline.caps == ("ERSTMALS", "ONLINE!")
    assert brief.device is not None and brief.device.product_asset_id == "pi"
    assert brief.products == ("sk", "ar")  # the phone product is not repeated
    assert brief.badge is not None and brief.logo_asset_id == "lidl"


def test_a_stored_thumbnail_is_reused() -> None:
    ex = executors(
        SETTINGS, thumbnail_designer=Recorder(designed(created=False)), reactions=Reactions(("me",))
    )
    assert ex.thumbnail_design(job(K.THUMBNAIL_DESIGN, STYLE)).status is TaskStatus.REUSED


def test_the_best_footage_moment_is_the_fallback_reaction() -> None:
    picture = SimpleNamespace(picture=make_asset("frame"))
    suggester = Recorder(Ok((picture,)))
    designer = Recorder(designed())
    ex = executors(
        SETTINGS,
        thumbnail_designer=designer,
        reactions=Reactions(()),
        reaction_suggester=SimpleNamespace(execute=lambda ids, ctx, limit: suggester.execute(ids)),
    )
    result = ex.thumbnail_design(job(K.THUMBNAIL_DESIGN, STYLE))
    assert result.status is TaskStatus.SUCCEEDED
    assert suggester.commands == [("a",)]
    assert designer.commands[0].brief.reaction_asset_id == "frame"


def test_an_invalid_price_fails_with_the_reason() -> None:
    ex = executors(
        replace(SETTINGS, thumbnail_device_price="79.99 €"),
        thumbnail_designer=Recorder(designed()),
        reactions=Reactions(("me",)),
    )
    result = ex.thumbnail_design(job(K.THUMBNAIL_DESIGN, STYLE))
    assert result.status is TaskStatus.FAILED
    assert "79,99 €" in result.detail
