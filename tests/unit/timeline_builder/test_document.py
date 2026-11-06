"""The stored form: one JSON document, exact, strict, versioned."""

import json
from typing import Any

import pytest

from media_house.modules.timeline_builder.domain.errors import InvalidBuiltDocument
from media_house.modules.timeline_builder.domain.human import HumanEdit
from media_house.modules.timeline_builder.domain.serialization import (
    timeline_from_json,
    timeline_to_json,
)
from media_house.modules.timeline_builder.domain.values import (
    DOCUMENT_TYPE,
    SCHEMA_VERSION,
    EditKind,
)
from tests.unit.edit_plan_compiler import fixtures as cf
from tests.unit.timeline_builder import fixtures as f
from tests.unit.timeline_builder.test_incremental_and_people import edit, modify


class TestRoundTrip:
    def test_a_built_timeline_reads_back_equal(self) -> None:
        t = f.built(f.facts_of(vfx=cf.vfx()))

        assert timeline_from_json(timeline_to_json(t)) == t

    def test_a_timeline_with_a_persons_work_reads_back_equal(self) -> None:
        t = f.built()
        edited = edit(
            t, modify("a:music:seg1", gain_db="-20"), HumanEdit(EditKind.DELETE, "o:graphic:g1")
        )

        assert timeline_from_json(timeline_to_json(edited)) == edited

    def test_the_text_is_deterministic(self) -> None:
        assert timeline_to_json(f.built()) == timeline_to_json(f.built())

    def test_times_are_stored_exactly_never_as_floats(self) -> None:
        body = json.loads(timeline_to_json(f.built()))

        clip = next(c for t in body["tracks"] for c in t["video"] if c["clip_id"] == "v:pB")
        assert clip["timeline_start"] == {"numerator": 4, "denominator": 1}
        assert body["document_type"] == DOCUMENT_TYPE and body["schema_version"] == SCHEMA_VERSION


class TestStrictReading:
    def doc(self) -> dict[str, Any]:
        body: dict[str, Any] = json.loads(timeline_to_json(f.built()))
        return body

    def test_not_json_is_refused(self) -> None:
        with pytest.raises(InvalidBuiltDocument, match="JSON"):
            timeline_from_json("{")

    def test_another_document_type_is_refused(self) -> None:
        body = self.doc()
        body["document_type"] = "timeline_specification"

        with pytest.raises(InvalidBuiltDocument, match="not a built timeline"):
            timeline_from_json(json.dumps(body))

    def test_another_schema_version_is_refused_not_reinterpreted(self) -> None:
        body = self.doc()
        body["schema_version"] = 99

        with pytest.raises(InvalidBuiltDocument, match="schema version"):
            timeline_from_json(json.dumps(body))

    def test_an_unknown_field_is_refused(self) -> None:
        body = self.doc()
        body["surprise"] = 1

        with pytest.raises(InvalidBuiltDocument, match="unknown fields"):
            timeline_from_json(json.dumps(body))

    def test_a_missing_field_is_refused(self) -> None:
        body = self.doc()
        del body["settings"]

        with pytest.raises(InvalidBuiltDocument, match="missing field"):
            timeline_from_json(json.dumps(body))

    def test_an_unknown_enum_member_is_refused(self) -> None:
        body = self.doc()
        body["status"] = "perfect"

        with pytest.raises(InvalidBuiltDocument, match="unknown BuildStatus"):
            timeline_from_json(json.dumps(body))

    def test_a_wrong_type_is_refused(self) -> None:
        body = self.doc()
        body["settings"]["width"] = "wide"

        with pytest.raises(InvalidBuiltDocument, match="expected int"):
            timeline_from_json(json.dumps(body))
