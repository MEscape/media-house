"""The stored document, the cache identity and relationship candidates."""

import json
from dataclasses import replace
from typing import Any

import pytest

from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.errors import (
    InvalidInspectionConfig,
    InvalidInspectionDocument,
)
from media_house.modules.media_inspection.domain.model import (
    DecodeMessage,
    Integrity,
    MediaInspection,
    TimecodeInfo,
)
from media_house.modules.media_inspection.domain.relations import (
    MediaSummary,
    RelationKind,
    find_relations,
)
from media_house.modules.media_inspection.domain.serialization import from_json, to_json
from media_house.modules.media_inspection.domain.timecode import Timecode
from media_house.modules.media_inspection.domain.values import (
    INSPECTION_VERSION,
    SCHEMA_VERSION,
    Certainty,
    Depth,
    FrameRateMode,
)
from tests.support.inspection_fakes import audio, inspect, observed, timing, video


def rich_inspection() -> MediaInspection:
    media = observed(
        (video(timing=timing(mode=FrameRateMode.VARIABLE, dropped_frames=2)),),
        (audio(),),
        timecode=TimecodeInfo(Timecode(1, 2, 3, 4, drop_frame=True), "video_stream", 3723.12),
        integrity=Integrity(True, True, (DecodeMessage("oops", 3),), 3),
        raw_metadata={"format": {"name": "mp4"}, "streams": [{"index": 0, "x": [1, 2.5, None]}]},
    )
    return inspect(media)


class TestDocument:
    def test_everything_survives_a_round_trip(self) -> None:
        original = rich_inspection()

        assert from_json(to_json(original)) == original

    def test_the_document_names_its_type_and_versions(self) -> None:
        document: dict[str, Any] = json.loads(to_json(rich_inspection()))

        assert document["document_type"] == "media_inspection"
        assert document["schema_version"] == SCHEMA_VERSION
        assert document["inspection_version"] == INSPECTION_VERSION
        for section in (
            "container",
            "video_streams",
            "audio_streams",
            "timecode",
            "production",
            "integrity",
            "synchronization",
            "findings",
            "status",
            "raw_metadata",
        ):
            assert section in document

    def test_exact_values_are_stored_as_fractions_and_origins_as_words(self) -> None:
        document: dict[str, Any] = json.loads(to_json(rich_inspection()))

        stream = document["video_streams"][0]
        assert stream["frame_rate"] == {"value": "30/1", "provenance": "declared"}
        assert stream["time_base"] == "1/15360"

    def test_a_newer_schema_is_refused_rather_than_misread(self) -> None:
        document = json.loads(to_json(rich_inspection()))
        document["schema_version"] = SCHEMA_VERSION + 1

        with pytest.raises(InvalidInspectionDocument):
            from_json(json.dumps(document))

    def test_unknown_extra_fields_of_the_same_schema_are_ignored(self) -> None:
        document = json.loads(to_json(rich_inspection()))
        document["added_later"] = {"x": 1}
        document["video_streams"][0]["added_later"] = True

        assert from_json(json.dumps(document)) == rich_inspection()

    @pytest.mark.parametrize("text", ["", "{", "[]", '{"document_type": "transcript"}', "null"])
    def test_other_documents_and_garbage_are_refused(self, text: str) -> None:
        with pytest.raises(InvalidInspectionDocument):
            from_json(text)

    def damaged(self, edit: Any) -> str:
        document = json.loads(to_json(rich_inspection()))
        edit(document)
        return json.dumps(document)

    @pytest.mark.parametrize(
        "edit",
        [
            lambda d: d["video_streams"][0].pop("geometry"),
            lambda d: d["video_streams"][0].update(index="zero"),
            lambda d: d["video_streams"][0]["frame_rate"].update(provenance="guessed"),
            lambda d: d["video_streams"][0]["frame_rate"].update(value="0/0"),
            lambda d: d["video_streams"][0]["frame_rate"].update(value=None),
            lambda d: d.update(created_at="yesterday"),
            lambda d: d.update(depth="shallow"),
            lambda d: d["findings"][0].update(severity="loud"),
            lambda d: d.update(findings="none"),
            lambda d: d["status"].update(verdict=5),
        ],
    )
    def test_damaged_content_is_reported_as_an_invalid_document(self, edit: Any) -> None:
        with pytest.raises(InvalidInspectionDocument):
            from_json(self.damaged(edit))


class TestConfig:
    def test_every_field_is_part_of_the_cache_identity(self) -> None:
        base = InspectionConfig()
        variants = [
            replace(base, depth=Depth.PROBE),
            replace(base, interval_tolerance=0.05),
            replace(base, variable_rate_fraction=0.02),
            replace(base, discontinuity_seconds=2.0),
            replace(base, sync_tolerance_seconds=0.02),
            replace(base, duration_tolerance_seconds=1.0),
            replace(base, low_bits_per_pixel=0.05),
            replace(base, frame_rate_tolerance=0.02),
            replace(base, decode_message_limit=5),
        ]

        identities = {json.dumps(v.fingerprint_config("p"), sort_keys=True) for v in variants}

        assert len(identities) == len(variants)
        assert json.dumps(base.fingerprint_config("p"), sort_keys=True) not in identities

    def test_tool_identity_and_inspection_version_are_part_of_it(self) -> None:
        config = InspectionConfig().fingerprint_config("ffprobe 7")

        assert config["probe"] == "ffprobe 7"
        assert config["inspection_version"] == INSPECTION_VERSION
        assert InspectionConfig().fingerprint_config("ffprobe 8") != config

    @pytest.mark.parametrize(
        "changes",
        [
            {"interval_tolerance": 0.0},
            {"variable_rate_fraction": 0.9},
            {"sync_tolerance_seconds": -1.0},
            {"discontinuity_seconds": float("nan")},
            {"low_bits_per_pixel": 0.0},
            {"decode_message_limit": 0},
        ],
    )
    def test_nonsense_thresholds_are_refused(self, changes: dict[str, float]) -> None:
        with pytest.raises(InvalidInspectionConfig):
            InspectionConfig(**changes)  # type: ignore[arg-type]


def summary(asset_id: str, **changes: Any) -> MediaSummary:
    base = MediaSummary(
        asset_id=asset_id,
        checksum=f"sum-{asset_id}",
        has_video=True,
        has_audio=True,
        duration=60.0,
        width=1920,
        height=1080,
        container=("mp4",),
        video_codec="h264",
    )
    return replace(base, **changes)


class TestRelations:
    def test_recorded_derivation_is_a_fact_in_both_directions(self) -> None:
        source = summary("src")
        derived = summary("derived", source_asset_id="src", has_video=False)

        from_source = find_relations(source, [derived])
        from_derived = find_relations(derived, [source])

        assert (from_source[0].kind, from_source[0].certainty) == (
            RelationKind.DERIVATIVE,
            Certainty.MEASURED,
        )
        assert from_derived[0].kind is RelationKind.SOURCE
        assert from_source[0].confidence == 1.0

    def test_identical_bytes_are_an_exact_duplicate(self) -> None:
        found = find_relations(summary("a"), [summary("b", checksum="sum-a")])

        assert found[0].kind is RelationKind.EXACT_DUPLICATE

    def test_same_length_and_picture_size_is_a_possible_duplicate_never_certain(self) -> None:
        found = find_relations(summary("a"), [summary("b", duration=60.02)])

        assert found[0].kind is RelationKind.POSSIBLE_DUPLICATE
        assert found[0].certainty is Certainty.POSSIBLE
        assert 0.5 < found[0].confidence <= 0.9

    def test_a_different_length_is_not_a_duplicate(self) -> None:
        assert find_relations(summary("a"), [summary("b", duration=75.0)]) == ()

    def test_a_smaller_copy_of_the_same_length_is_a_possible_proxy(self) -> None:
        proxy = summary("p", width=960, height=540, duration=60.0, container=("mov",))

        found = find_relations(summary("a"), [proxy])

        assert found[0].kind is RelationKind.POSSIBLE_PROXY
        assert found[0].evidence["other_is"] == "lower_resolution"
        assert found[0].confidence < 1.0

    def test_a_different_shape_is_not_a_proxy(self) -> None:
        other = summary("p", width=1080, height=1920)

        assert find_relations(summary("a"), [other]) == ()

    def test_silent_video_and_audio_only_of_the_same_length_are_companions(self) -> None:
        picture = summary("v", has_audio=False)
        sound = summary("s", has_video=False, width=None, height=None)

        assert find_relations(picture, [sound])[0].kind is RelationKind.POSSIBLE_COMPANION
        assert find_relations(sound, [picture])[0].kind is RelationKind.POSSIBLE_COMPANION

    def test_unrelated_assets_and_the_asset_itself_are_ignored(self) -> None:
        subject = summary("a")

        assert (
            find_relations(subject, [subject, summary("z", duration=5.0, width=320, height=240)])
            == ()
        )

    def test_strongest_candidates_come_first(self) -> None:
        close = summary("close", duration=60.0)
        far = summary("far", duration=60.4, container=("mkv",), video_codec="hevc")

        found = find_relations(summary("a"), [far, close])

        assert [c.asset_id for c in found] == ["close", "far"]
