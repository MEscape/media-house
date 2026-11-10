"""The static catalogue must match the code it summarises (it is a copy, not a guess)."""

from pathlib import Path

from media_house.modules.control_center.application.catalogue import (
    DELIVER_TASK,
    LAYERS,
    TASK_OWNERS,
    all_modules,
    dependants,
    find_module,
    task_label,
)
from media_house.modules.media_pipeline.domain.plan import STAGES

MODULES_DIR = Path(__file__).resolve().parents[3] / "src" / "media_house" / "modules"


def test_every_backend_module_is_catalogued_exactly_once() -> None:
    names = [m.name for m in all_modules()]
    assert len(names) == len(set(names))
    on_disk = {p.parent.name for p in MODULES_DIR.glob("*/README.md")}
    assert set(names) == on_disk


def test_six_layers_plus_shared() -> None:
    assert [layer.key for layer in LAYERS] == [
        "layer1",
        "layer2",
        "layer3",
        "layer4",
        "layer5",
        "layer6",
        "shared",
    ]


def test_pipeline_tasks_match_media_pipeline_stage_table() -> None:
    copied = {m.pipeline.task: m.pipeline for m in all_modules() if m.pipeline is not None}
    copied[DELIVER_TASK.task] = DELIVER_TASK
    assert set(copied) == {kind.value for kind in STAGES}
    for kind, stage in STAGES.items():
        task = copied[kind.value]
        assert task.lane == stage.lane.value, kind
        assert set(task.needs) == {k.value for k in stage.needs}, kind
        assert set(task.wants) == {k.value for k in stage.wants}, kind


def test_reads_contracts_match_the_source_imports() -> None:
    for module in all_modules():
        found: set[str] = set()
        for path in (MODULES_DIR / module.name).rglob("*.py"):
            if "presentation" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for other in {m.name for m in all_modules()} - {module.name}:
                if f"media_house.modules.{other}.application.contracts" in text:
                    found.add(other)
        assert set(module.reads_contracts) == found, module.name


def test_every_module_states_its_limits() -> None:
    assert all(m.limits and m.description for m in all_modules())


def test_resolve_modules_say_they_never_ran_against_resolve() -> None:
    for name in ("resolve_adapter", "resolve_export", "render_manager"):
        module = find_module(name)
        assert module is not None
        assert "real DaVinci Resolve" in module.validation.value


def test_dependency_helpers() -> None:
    assert TASK_OWNERS["audio_analysis"] == "audio_intelligence"
    assert task_label("story") == "story (story_architecture)"
    users = dict(dependants("audio_intelligence"))
    assert users["content_intelligence"] is True  # needs the transcript
    assert users["pacing_intelligence"] is True
    assert users["caption_engine"] is False  # only uses it if available
    assert dependants("revision_engine") == ()  # not a pipeline task
