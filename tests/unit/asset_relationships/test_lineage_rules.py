"""The pure rules of recorded lineage: what is a media relationship, and what a query covers."""

import pytest

from media_house.modules.asset_relationships.application.lineage_projection import LineageSettings
from media_house.modules.asset_relationships.domain.lineage import (
    DerivativeFacts,
    LineageSkip,
    classify_derivative,
    origin_of,
    projection_scope,
)
from media_house.modules.asset_relationships.domain.taxonomy import LINEAGE_TYPES, STORED_TYPES
from media_house.modules.asset_relationships.domain.values import (
    RelationshipOrigin,
    RelationshipType,
)
from media_house.modules.audio_intelligence.domain.values import EXTRACTION_OPERATION

EXCLUDED = frozenset({"audio_extraction"})
DUPLICATE = RelationshipType.EXACT_DUPLICATE_OF
PROXY = RelationshipType.PROXY_OF
DERIVED = RelationshipType.DERIVED_FROM


def facts(
    *,
    is_media: bool = True,
    is_preview: bool = False,
    operation: str = "video_improvement",
    provenance: str | None = None,
    source: str = "src",
) -> DerivativeFacts:
    return DerivativeFacts(source, is_media, is_preview, operation, provenance)


class TestClassification:
    def test_a_media_derivative_is_derived_from(self) -> None:
        result = classify_derivative(facts(), EXCLUDED)

        assert (result.relationship_type, result.skip) == (DERIVED, None)

    def test_a_document_is_not_media_whatever_it_claims(self) -> None:
        result = classify_derivative(facts(is_media=False, provenance="src"), EXCLUDED)

        assert (result.relationship_type, result.skip) == (None, LineageSkip.NOT_MEDIA)

    def test_a_proxy_needs_the_provenance_and_the_derivation_to_agree(self) -> None:
        agree = classify_derivative(facts(is_preview=True, provenance="src"), EXCLUDED)
        disagree = classify_derivative(facts(is_preview=True, provenance="other"), EXCLUDED)

        assert agree.relationship_type is PROXY
        assert (disagree.relationship_type, disagree.skip) == (
            None,
            LineageSkip.UNTRUSTED_PROVENANCE,
        )

    def test_disagreeing_provenance_never_falls_back_to_derived_from(self) -> None:
        result = classify_derivative(facts(provenance="other"), EXCLUDED)

        assert result.relationship_type is None

    def test_a_preview_without_provenance_is_not_lineage(self) -> None:
        result = classify_derivative(facts(is_preview=True, operation="thumbnail"), EXCLUDED)

        assert (result.relationship_type, result.skip) == (None, LineageSkip.PREVIEW)

    def test_an_excluded_operation_is_an_internal_intermediate(self) -> None:
        result = classify_derivative(facts(operation="audio_extraction"), EXCLUDED)

        assert (result.relationship_type, result.skip) == (None, LineageSkip.EXCLUDED_OPERATION)

    def test_exclusions_are_configurable(self) -> None:
        assert (
            classify_derivative(facts(operation="audio_extraction"), frozenset()).relationship_type
            is DERIVED
        )
        assert (
            classify_derivative(
                facts(operation="video_improvement"), frozenset({"video_improvement"})
            ).skip
            is LineageSkip.EXCLUDED_OPERATION
        )

    def test_a_proxy_is_reported_as_a_proxy_not_also_as_a_derivative(self) -> None:
        result = classify_derivative(facts(provenance="src", operation="proxy"), EXCLUDED)

        assert result.relationship_type is PROXY


class TestSettingsAreTiedToTheRealOperations:
    def test_the_default_exclusion_is_the_real_audio_extraction_operation(self) -> None:
        """A rename in audio_intelligence must fail here, not silently start reporting WAVs."""
        assert LineageSettings().excluded_operations == frozenset({EXTRACTION_OPERATION})

    def test_the_scan_cap_is_a_whole_number_of_pages(self) -> None:
        assert LineageSettings().max_derivative_pages >= 1


class TestOrigin:
    def test_a_proxy_comes_from_proxy_provenance_the_rest_from_the_library(self) -> None:
        assert origin_of(PROXY) is RelationshipOrigin.PROXY_RECORD
        assert origin_of(DERIVED) is RelationshipOrigin.LIBRARY_RECORD
        assert origin_of(DUPLICATE) is RelationshipOrigin.LIBRARY_RECORD


class TestTypes:
    def test_lineage_and_claims_partition_the_vocabulary(self) -> None:
        assert {DUPLICATE, PROXY, DERIVED} == LINEAGE_TYPES
        assert set(RelationshipType) == LINEAGE_TYPES | STORED_TYPES
        assert not LINEAGE_TYPES & STORED_TYPES


class TestScope:
    def scope(
        self,
        *,
        types: set[RelationshipType] | None = None,
        origins: set[RelationshipOrigin] | None = None,
        statuses: bool = False,
        include: bool = True,
    ) -> frozenset[RelationshipType]:
        return projection_scope(
            types=types or set(),
            origins=origins or set(),
            statuses_filtered=statuses,
            include_projected=include,
        )

    def test_no_restriction_asks_for_all_lineage(self) -> None:
        assert self.scope() == LINEAGE_TYPES

    def test_a_types_filter_narrows_it_and_ignores_claim_types(self) -> None:
        assert self.scope(types={PROXY, RelationshipType.ALTERNATE_TAKE_OF}) == {PROXY}
        assert self.scope(types={RelationshipType.ALTERNATE_TAKE_OF}) == frozenset()

    def test_a_status_filter_asks_for_claims_only(self) -> None:
        assert self.scope(statuses=True) == frozenset()

    def test_projection_can_be_switched_off(self) -> None:
        assert self.scope(include=False) == frozenset()

    def test_origins_pick_the_authority(self) -> None:
        assert self.scope(origins={RelationshipOrigin.PROXY_RECORD}) == {PROXY}
        assert self.scope(origins={RelationshipOrigin.LIBRARY_RECORD}) == {DUPLICATE, DERIVED}
        assert (
            self.scope(origins={RelationshipOrigin.USER, RelationshipOrigin.DETECTOR})
            == frozenset()
        )

    @pytest.mark.parametrize("types", [{DUPLICATE}, {DERIVED}])
    def test_types_and_origins_both_apply(self, types: set[RelationshipType]) -> None:
        assert self.scope(types=types, origins={RelationshipOrigin.PROXY_RECORD}) == frozenset()
