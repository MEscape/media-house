"""Resolve, reuse, request, degrade: upstream inputs come from their owners, never from here."""

from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from media_house.modules.media_inspection.application.contracts import (
    InspectionResult,
    InspectMediaCommand,
    MediaInspection,
)
from media_house.modules.media_inspection.domain.model import Integrity
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    MediaAssetDto,
)
from media_house.modules.media_library.application.dto import DerivationDto
from media_house.modules.video_improvement.application.contracts import (
    OperationRecord,
    ProcessingProvenance,
    ProcessingStage,
    StageStatus,
)
from media_house.modules.video_intelligence.application.resolver import UpstreamResolver
from media_house.modules.video_intelligence.domain.errors import (
    InspectionUnavailable,
    NoVideoStream,
    UnreadableVideo,
)
from media_house.modules.video_intelligence.domain.values import (
    InputSource,
    MeasuredOn,
    Stabilization,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, NotFoundError, Ok
from tests.support import inspection_fakes as fakes
from tests.support.asset_dto import make_asset


def provenance(*stages: tuple[ProcessingStage, str, StageStatus]) -> ProcessingProvenance:
    return ProcessingProvenance(
        source_asset_id="clip",
        source_profile="generic",
        source_profile_version=1,
        source_profile_origin="explicit",
        source_profile_evidence="",
        processing_profile="natural",
        input_color_space="bt709",
        working_color_space="linear",
        output_color_space="bt709",
        operations=tuple(
            OperationRecord(stage, name, status, "") for stage, name, status in stages
        ),
        engines={},
        processing_version=1,
        facts_source="inspection",
        before={},
        after={},
    )


def improved_asset(prov: ProcessingProvenance | None) -> MediaAssetDto:
    return make_asset(
        "improved",
        role=AssetRole.DERIVED,
        derivation=DerivationDto("clip", "video_improvement", 1, {}, "f" * 64),
        metadata=prov.to_metadata() if prov else {},
    )


@dataclass
class FakeInspector:
    """Stands in for ``MediaInspector``: records what the resolver asked of the owner."""

    stored: MediaInspection | None = None
    on_request: MediaInspection | Err[NotFoundError] | None = None
    finds: int = 0
    requests: list[InspectMediaCommand] = field(default_factory=list)

    def find(self, asset_id: str, config: object = None) -> InspectionResult | None:
        self.finds += 1
        return self._result(self.stored)

    def execute(self, command: InspectMediaCommand, ctx: JobContext) -> Any:
        self.requests.append(command)
        if isinstance(self.on_request, Err):
            return self.on_request
        assert self.on_request is not None
        return Ok(self._result(self.on_request))

    @staticmethod
    def _result(inspection: MediaInspection | None) -> InspectionResult | None:
        if inspection is None:
            return None
        return InspectionResult(inspection, make_asset("inspection-doc"), created=False)


def inspection(**video_changes: object) -> MediaInspection:
    return fakes.inspect(fakes.observed((fakes.video(**video_changes),)))


RESOLVE = JobContext.detached()


class TestInspectionFacts:
    def test_a_stored_inspection_is_reused_and_the_owner_is_not_asked(self) -> None:
        owner = FakeInspector(stored=inspection())

        inputs = UpstreamResolver(owner).resolve(make_asset(), RESOLVE)

        used = {u.name: u for u in inputs.used}
        assert used["media_inspection"].source is InputSource.REUSED
        assert used["media_inspection"].asset_id == "inspection-doc"
        assert owner.requests == []

    def test_a_missing_inspection_is_requested_from_its_owner(self) -> None:
        owner = FakeInspector(on_request=inspection())

        inputs = UpstreamResolver(owner).resolve(make_asset(), RESOLVE)

        assert owner.requests == [InspectMediaCommand("clip")]
        assert {u.name: u.source for u in inputs.used}["media_inspection"] is InputSource.REQUESTED

    def test_an_owner_that_cannot_deliver_fails_clearly_instead_of_guessing(self) -> None:
        owner = FakeInspector(on_request=Err(NotFoundError("gone")))

        with pytest.raises(InspectionUnavailable):
            UpstreamResolver(owner).resolve(make_asset(), RESOLVE)

    def test_an_unreadable_file_is_reported_as_such(self) -> None:
        broken = fakes.observed(
            (fakes.video(),),
            integrity=Integrity(
                readable=False, decode_checked=False, read_error="moov atom not found"
            ),
        )
        owner = FakeInspector(stored=fakes.inspect(broken))

        with pytest.raises(UnreadableVideo, match="moov atom"):
            UpstreamResolver(owner).resolve(make_asset(), RESOLVE)

    def test_media_without_video_is_refused(self) -> None:
        owner = FakeInspector(stored=fakes.inspect(fakes.observed((), (fakes.audio(),))))

        with pytest.raises(NoVideoStream):
            UpstreamResolver(owner).resolve(make_asset(), RESOLVE)

    def test_the_facts_come_from_the_inspection_in_display_orientation(self) -> None:
        owner = FakeInspector(stored=inspection())

        source = UpstreamResolver(owner).resolve(make_asset(), RESOLVE).source

        assert (source.width, source.height) == (1920, 1080)
        assert source.frame_rate is not None and source.frame_rate.value == pytest.approx(30.0)
        assert source.rotation == 0 and source.variable_frame_rate is False
        assert source.color_transfer == "bt709"


class TestOptionalInputs:
    def test_unavailable_optional_inputs_are_recorded_not_omitted(self) -> None:
        inputs = UpstreamResolver(FakeInspector(stored=inspection())).resolve(make_asset(), RESOLVE)

        used = {u.name: u for u in inputs.used}
        assert used["black_frame_findings"].source is InputSource.NOT_AVAILABLE
        assert used["project_brief"].source is InputSource.NOT_AVAILABLE
        assert used["audio_intelligence"].source is InputSource.NOT_APPLICABLE
        assert all(u.detail for u in used.values() if u.source is not InputSource.REUSED)


class TestProcessingHistory:
    def resolve(self, asset: MediaAssetDto):  # type: ignore[no-untyped-def]
        return UpstreamResolver(FakeInspector(stored=inspection())).resolve(asset, RESOLVE)

    def test_an_original_is_measured_on_the_original(self) -> None:
        history = self.resolve(make_asset()).history

        assert history.measured_on is MeasuredOn.ORIGINAL
        assert history.stabilized is Stabilization.NO and history.derived_from_asset_id is None

    def test_an_improved_version_reuses_the_published_provenance(self) -> None:
        prov = provenance(
            (ProcessingStage.COLOR, "color_grade", StageStatus.APPLIED),
            (ProcessingStage.DENOISE, "denoise", StageStatus.SKIPPED),
        )

        inputs = self.resolve(improved_asset(prov))

        assert inputs.history.measured_on is MeasuredOn.IMPROVED
        assert inputs.history.operations == ("color_grade",)  # skipped stages did not happen
        assert inputs.history.derived_from_asset_id == "clip"
        assert inputs.history.stabilized is Stabilization.NO
        assert {u.name: u.source for u in inputs.used}["processing_history"] is InputSource.REUSED

    def test_a_derived_asset_without_a_record_is_unknown_not_guessed(self) -> None:
        inputs = self.resolve(improved_asset(None))

        assert inputs.history.measured_on is MeasuredOn.UNKNOWN
        assert inputs.history.stabilized is Stabilization.UNKNOWN
        assert inputs.history.operations == ()
        used = {u.name: u for u in inputs.used}
        assert used["processing_history"].source is InputSource.NOT_AVAILABLE

    def test_a_stabilizing_stage_is_recognised_without_changing_the_analysis(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "media_house.modules.video_intelligence.application.resolver.STABILIZING_STAGES",
            frozenset({"color"}),
        )
        prov = provenance((ProcessingStage.COLOR, "grade", StageStatus.APPLIED))

        assert self.resolve(improved_asset(prov)).history.stabilized is Stabilization.YES

    def test_a_damaged_record_reads_as_unknown(self) -> None:
        asset = replace(improved_asset(None), metadata={"video_processing": {"schema_version": 99}})

        assert self.resolve(asset).history.measured_on is MeasuredOn.UNKNOWN
