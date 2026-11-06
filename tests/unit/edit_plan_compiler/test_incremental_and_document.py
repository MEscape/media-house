"""Incremental compilation, the stored document, the knobs and the pipeline hand-over."""

import dataclasses
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from media_house.modules.edit_plan_compiler.application.compile_timeline import _stale
from media_house.modules.edit_plan_compiler.domain.engine import compile_timeline
from media_house.modules.edit_plan_compiler.domain.errors import (
    InvalidCompilerConfig,
    InvalidTimelineDocument,
)
from media_house.modules.edit_plan_compiler.domain.profile import CompilerConfig
from media_house.modules.edit_plan_compiler.domain.serialization import (
    spec_from_json,
    spec_to_json,
)
from media_house.modules.edit_plan_compiler.domain.values import COMPILER_VERSION
from tests.unit.edit_plan_compiler import fixtures as f


def build(config: CompilerConfig | None = None, previous=None, version: int = 1, **kw):  # type: ignore[no-untyped-def]
    return compile_timeline(
        f.inputs(**kw), config or CompilerConfig(), meta=f.meta(version), previous=previous
    )


class TestIncrementalCompilation:
    def test_a_first_compilation_reuses_nothing(self) -> None:
        spec = build().spec

        assert spec.compilation.units_reused == 0 and spec.compilation.units_total == 8
        assert set(spec.compilation.change.added) >= {"v:pA", "o:graphic:g1"}

    def test_unchanged_inputs_reuse_every_unit_and_change_nothing(self) -> None:
        first = build().spec

        second = build(previous=first, version=2).spec

        assert second.compilation.units_reused == second.compilation.units_total
        change = second.compilation.change
        assert change.added == change.removed == change.changed == ()
        assert change.unchanged > 10
        assert second.compilation.spec_hash == first.compilation.spec_hash

    def test_one_changed_graphic_recompiles_only_the_graphics_unit(self) -> None:
        first = build().spec
        plan = f.graphics()
        moved = replace(plan, graphics=(replace(plan.graphics[0], timeline_start=f.r(3)),))

        second = build(previous=first, version=2, graphics=moved).spec

        assert second.compilation.units_reused == second.compilation.units_total - 1
        assert second.compilation.change.changed == ("o:graphic:g1",)
        assert second.compilation.change.added == second.compilation.change.removed == ()

    def test_one_changed_clip_recompiles_its_unit_and_its_neighbour_transition(self) -> None:
        first = build().spec
        assets = dict(f.assets())
        assets["rec1"] = f.asset("rec1", duration=61)  # a file that changed on disk

        second = build(previous=first, version=2, assets=assets).spec

        assert 0 < second.compilation.units_reused < second.compilation.units_total

    def test_a_changed_config_reuses_nothing(self) -> None:
        first = build().spec

        second = build(CompilerConfig(sample_rate=44_100), previous=first, version=2).spec

        assert second.compilation.units_reused == 0
        assert second.compilation.config_hash != first.compilation.config_hash

    def test_a_removed_item_is_named_in_the_change_summary(self) -> None:
        first = build().spec

        second = build(previous=first, version=2, graphics=None).spec

        assert "o:graphic:g1" in second.compilation.change.removed

    def test_a_reused_unit_keeps_its_findings(self) -> None:
        plan = f.captions()
        seg = replace(plan.segments[0], render_status="failed", asset_id=None)
        broken = replace(plan, segments=(seg, plan.segments[1]))
        first = build(captions=broken).spec

        second = build(captions=broken, previous=first, version=2).spec

        assert [i.code for i in second.issues] == [i.code for i in first.issues]

    def test_provenance_names_the_versions_and_the_unit_digests(self) -> None:
        spec = build().spec

        c = spec.compilation
        assert (
            c.compiler_version == COMPILER_VERSION
            and c.input_hash
            and c.dependency_hash
            and c.spec_hash
        )
        assert dict(c.unit_digests)["graphics"]


class TestDocument:
    def test_the_specification_round_trips_exactly(self) -> None:
        spec = build().spec

        assert spec_from_json(spec_to_json(spec)) == spec

    def test_times_are_stored_exactly_never_as_floats(self) -> None:
        text = spec_to_json(
            compile_timeline(f.inputs(vfx=None), CompilerConfig(), meta=f.meta()).spec
        )

        body = json.loads(text)
        start = body["transitions"][0]["timeline_start"]
        assert start == {"numerator": 39, "denominator": 10}
        assert body["document_type"] == "timeline_specification"

    def test_an_unknown_field_is_refused(self) -> None:
        body = json.loads(spec_to_json(build().spec))
        body["surprise"] = 1

        with pytest.raises(InvalidTimelineDocument):
            spec_from_json(json.dumps(body))

    def test_another_schema_version_is_refused(self) -> None:
        body = json.loads(spec_to_json(build().spec))
        body["schema_version"] = 99

        with pytest.raises(InvalidTimelineDocument):
            spec_from_json(json.dumps(body))

    def test_a_foreign_document_is_refused(self) -> None:
        with pytest.raises(InvalidTimelineDocument):
            spec_from_json(json.dumps({"document_type": "transition_plan"}))

    def test_the_specification_names_no_timeline_application(self) -> None:
        text = spec_to_json(build().spec).lower()

        assert "resolve" not in text.replace("resolved", "") and "davinci" not in text


class TestKnobs:
    def test_every_knob_carries_ui_metadata(self) -> None:
        for field in dataclasses.fields(CompilerConfig):
            if field.name == "version":
                continue
            assert {"label", "kind"} <= set(field.metadata), field.name

    def test_ranges_in_the_metadata_match_the_checks(self) -> None:
        by_name = {f.name: f.metadata for f in dataclasses.fields(CompilerConfig)}
        rate = by_name["sample_rate"]

        with pytest.raises(InvalidCompilerConfig):
            CompilerConfig(sample_rate=int(rate["min"]) - 1).check()
        with pytest.raises(InvalidCompilerConfig):
            CompilerConfig(sample_rate=int(rate["max"]) + 1).check()
        CompilerConfig(sample_rate=int(rate["min"])).check()

    def test_every_knob_is_part_of_the_fingerprint(self) -> None:
        assert set(CompilerConfig().fingerprint()) == {
            f.name for f in dataclasses.fields(CompilerConfig)
        }

    def test_a_non_positive_frame_rate_is_refused(self) -> None:
        with pytest.raises(InvalidCompilerConfig):
            CompilerConfig(timeline_frame_rate=f.r(0, 1)).check()


STYLE: Any = SimpleNamespace(fingerprint=f.STYLE_FP)


class TestStoredPlans:
    """A stored plan is an input only when it belongs to this edit and this style."""

    def plan(self, *, edit: object | None, style: str | None) -> SimpleNamespace:
        bound = None if style is None else SimpleNamespace(fingerprint=style)
        return SimpleNamespace(edit=edit, style=bound)

    def test_a_plan_for_this_edit_and_style_is_accepted(self) -> None:
        plan = self.plan(
            edit=SimpleNamespace(edit_plan_id="edit-1", plan_version=1), style=f.STYLE_FP
        )

        assert _stale(plan, f.source(), STYLE) == ""

    def test_a_plan_of_an_older_edit_version_is_stale(self) -> None:
        plan = self.plan(
            edit=SimpleNamespace(edit_plan_id="edit-1", plan_version=0), style=f.STYLE_FP
        )

        assert "another edit" in _stale(plan, f.source(), STYLE)

    def test_a_plan_made_with_another_style_is_stale(self) -> None:
        plan = self.plan(
            edit=SimpleNamespace(edit_plan_id="edit-1", plan_version=1), style="0" * 64
        )

        assert "another channel style" in _stale(plan, f.source(), STYLE)

    def test_a_no_speech_plan_does_not_fit_an_edit(self) -> None:
        plan = self.plan(edit=None, style=f.STYLE_FP)

        assert _stale(plan, f.source(), STYLE) != ""
