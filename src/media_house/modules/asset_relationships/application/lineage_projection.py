"""Recorded lineage, projected at read time from its authorities and never stored.

The authorities are the Media Library (derivation records, checksums) and proxy provenance. This
class asks them through their public contracts and answers ``exact_duplicate_of``, ``proxy_of`` and
``derived_from`` for ONE asset, so a replaced or deleted proxy cannot leave a stale relationship
behind: there is nothing here to go stale.

Bounds: derivatives are read in library pages (200 each) up to ``max_derivative_pages``; beyond that
the answer says ``truncated``. Nothing scans the library: duplicates of an original are only looked
for among its own derivatives (the library cannot list every asset sharing its bytes), which the
caller reports as ``EXACT_DUPLICATE_REVERSE_PARTIAL``.
"""

from collections import Counter
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from media_house.modules.asset_relationships.application.dto import RelationshipDto
from media_house.modules.asset_relationships.application.mapping import (
    endpoint_states,
    evidence_dto,
)
from media_house.modules.asset_relationships.application.ports import LineageLibrary
from media_house.modules.asset_relationships.domain.evidence import RelationshipEvidence
from media_house.modules.asset_relationships.domain.lineage import (
    LINEAGE_PROJECTION_VERSION,
    DerivativeFacts,
    FindingKind,
    LineageFinding,
    LineageSkip,
    classify_derivative,
    origin_of,
)
from media_house.modules.asset_relationships.domain.values import (
    EvidenceKind,
    Grade,
    JsonValue,
    RelationshipId,
    RelationshipType,
)
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    AssetStatus,
    MediaAssetDto,
    MediaQuery,
    MediaType,
    SortField,
    SortOrder,
)
from media_house.modules.proxy_manager.application.contracts import (
    ProxyCatalog,
    ProxyProvenance,
    read_proxy_provenance,
)
from media_house.shared.errors import Err, InfrastructureError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

#: Who states the projected facts, as evidence providers.
LIBRARY = "media_library"
PROXIES = "proxy_manager"
#: Names the projection as the "detector" of an item (it detects nothing: it reports records).
BASIS_DETECTOR = "lineage_projection"

#: Marks a (source, profile) slot whose serving state could not be asked (never a real asset id).
_UNKNOWN = "<serving unknown>"
_MEDIA = frozenset({MediaType.VIDEO, MediaType.AUDIO, MediaType.IMAGE})
_DERIVATIVE_TYPES = frozenset({RelationshipType.DERIVED_FROM, RelationshipType.PROXY_OF})


@dataclass(frozen=True, slots=True)
class LineageSettings:
    """How recorded lineage is judged and bounded."""

    #: Media-bearing operations that are internal intermediates, not user-meaningful derivatives.
    #: ``audio_extraction`` is the WAV that audio intelligence cuts from a source's audio.
    excluded_operations: frozenset[str] = frozenset({"audio_extraction"})
    #: Library pages of derivatives read per query (200 each). Provisional, to be set by measuring.
    max_derivative_pages: int = 5


@dataclass(frozen=True, slots=True)
class ProjectedLineage:
    #: In relationship-id order, each id appearing once.
    items: tuple[RelationshipDto, ...]
    #: The derivative scan hit ``max_derivative_pages``; some derivatives may be missing.
    truncated: bool
    #: Problems found in the library's own records, sorted; every one is listed.
    findings: tuple[LineageFinding, ...] = ()
    #: ``proxy_manager`` failed, so ``serving`` could not be determined (it is ``None``).
    proxy_state_unavailable: bool = False
    #: ``Page.total`` of the first derivative-scan page (all derivatives, whatever their state);
    #: ``None`` when no scan ran. A changed total between two pages proves the derivatives changed;
    #: an unchanged total proves nothing.
    scan_total: int | None = None


class _Run:
    """The state of one projection: the items found and what was set aside."""

    def __init__(self) -> None:
        self.items: dict[str, RelationshipDto] = {}
        self.skipped: Counter[str] = Counter()
        self.truncated = False
        self.serving: dict[tuple[str, str], str | None] = {}
        self.findings: set[LineageFinding] = set()
        self.proxy_unavailable = False
        self.scan_total: int | None = None


class LineageProjection:
    """Answers recorded lineage for one asset from the library and proxy provenance."""

    def __init__(
        self,
        library: LineageLibrary,
        proxies: ProxyCatalog | None = None,
        settings: LineageSettings | None = None,
    ) -> None:
        self._library = library
        self._proxies = proxies
        self._settings = settings or LineageSettings()

    def project(
        self,
        asset: MediaAssetDto,
        scope: AbstractSet[RelationshipType],
        *,
        include_archived: bool,
    ) -> ProjectedLineage:
        """The lineage types in ``scope`` that touch ``asset``. An empty scope reads nothing."""
        run = _Run()
        if scope & _DERIVATIVE_TYPES:
            self._upward(asset, scope, include_archived, run)
        # derivatives matter for derived_from/proxy_of, and for duplicates only of an original
        if scope & _DERIVATIVE_TYPES or (
            RelationshipType.EXACT_DUPLICATE_OF in scope and asset.role is AssetRole.ORIGINAL
        ):
            self._downward(asset, scope, include_archived, run)
        if RelationshipType.EXACT_DUPLICATE_OF in scope and asset.role is not AssetRole.ORIGINAL:
            self._original_of(asset, include_archived, run)
        self._report(asset, run)
        return ProjectedLineage(
            tuple(run.items[i] for i in sorted(run.items)),
            run.truncated,
            tuple(sorted(run.findings, key=lambda f: (f.kind.value, f.asset_id))),
            run.proxy_unavailable,
            run.scan_total,
        )

    @staticmethod
    def _report(asset: MediaAssetDto, run: _Run) -> None:
        """Expected skips are routine (every inspected asset has documents): debug only."""
        if run.skipped:
            _log.debug("Lineage set aside", asset_id=asset.id, skipped=dict(run.skipped))
        if run.findings:
            _log.warning(
                "Lineage findings",
                asset_id=asset.id,
                findings=sorted(f"{f.kind.value}:{f.asset_id}" for f in run.findings),
            )
        if run.truncated:
            _log.info("Lineage derivative scan truncated", asset_id=asset.id)
        if run.proxy_unavailable:
            _log.warning("Proxy serving state unavailable", asset_id=asset.id)

    # ------------------------------------------------------------------ discovery
    def _upward(
        self,
        asset: MediaAssetDto,
        scope: AbstractSet[RelationshipType],
        include_archived: bool,
        run: _Run,
    ) -> None:
        """If ``asset`` is itself a derivative: how it relates to its source."""
        if asset.derivation is None:
            return
        source = self._library.get(asset.derivation.source_asset_id)
        if isinstance(source, Err):
            run.findings.add(LineageFinding(FindingKind.SOURCE_MISSING, asset.id))
            return
        if _visible(source.value, include_archived):
            self._derivative(asset, source.value, scope, run)

    def _downward(
        self,
        asset: MediaAssetDto,
        scope: AbstractSet[RelationshipType],
        include_archived: bool,
        run: _Run,
    ) -> None:
        """The derivatives of ``asset``, page by page up to the cap."""
        duplicates = (
            RelationshipType.EXACT_DUPLICATE_OF in scope and asset.role is AssetRole.ORIGINAL
        )
        for number in range(1, self._settings.max_derivative_pages + 1):
            page = self._library.search(
                MediaQuery(
                    source_asset_id=asset.id,
                    roles=frozenset(),
                    statuses=frozenset(),
                    sort_by=SortField.CREATED_AT,
                    sort_order=SortOrder.ASC,
                    page=number,
                    page_size=MediaQuery.MAX_PAGE_SIZE,
                )
            )
            if number == 1:
                run.scan_total = page.total
            for derivative in page.items:
                if not _visible(derivative, include_archived):
                    continue
                self._derivative(derivative, asset, scope, run)
                if duplicates and derivative.checksum == asset.checksum:
                    self._add(
                        run,
                        RelationshipType.EXACT_DUPLICATE_OF,
                        derivative,
                        asset,
                        [_checksum_evidence(derivative)],
                    )
            if not page.has_next:
                return
        run.truncated = True

    def _original_of(self, asset: MediaAssetDto, include_archived: bool, run: _Run) -> None:
        """A non-original asset whose bytes equal an original's: that original (complete)."""
        original = self._library.find_by_checksum(asset.checksum)
        if original is None or original.id == asset.id:
            return
        if _visible(original, include_archived):
            self._add(
                run,
                RelationshipType.EXACT_DUPLICATE_OF,
                asset,
                original,
                [_checksum_evidence(asset)],
            )

    # ------------------------------------------------------------------ one derivative
    def _derivative(
        self,
        derivative: MediaAssetDto,
        source: MediaAssetDto,
        scope: AbstractSet[RelationshipType],
        run: _Run,
    ) -> None:
        derivation = derivative.derivation
        if derivation is None:
            return
        provenance = read_proxy_provenance(derivative.metadata)
        verdict = classify_derivative(
            DerivativeFacts(
                source_asset_id=derivation.source_asset_id,
                is_media=derivative.media_type in _MEDIA,
                is_preview=derivative.role is AssetRole.PREVIEW,
                operation=derivation.operation,
                provenance_source_id=provenance.source_asset_id if provenance else None,
            ),
            self._settings.excluded_operations,
        )
        if verdict.skip is LineageSkip.UNTRUSTED_PROVENANCE:
            run.findings.add(LineageFinding(FindingKind.UNTRUSTED_PROVENANCE, derivative.id))
        elif verdict.skip is not None:
            run.skipped[verdict.skip.value] += 1
        if verdict.relationship_type is None or verdict.relationship_type not in scope:
            return
        evidence = [_derivation_evidence(derivative)]
        serving: bool | None = None
        if provenance is not None and verdict.relationship_type is RelationshipType.PROXY_OF:
            evidence.append(_provenance_evidence(derivative, provenance))
            serving = self._serving(derivative, provenance, run)
        self._add(run, verdict.relationship_type, derivative, source, evidence, serving)

    def _serving(self, proxy: MediaAssetDto, provenance: ProxyProvenance, run: _Run) -> bool | None:
        """Whether this proxy is the one its slot serves now; ``None`` without the catalog."""
        if self._proxies is None:
            return None
        key = (provenance.source_asset_id, provenance.profile)
        if key not in run.serving:
            try:
                served = self._proxies.find(*key)
            except InfrastructureError:
                # the answer degrades (serving is unknown) instead of the whole query failing
                run.proxy_unavailable = True
                run.serving[key] = _UNKNOWN
            else:
                run.serving[key] = served.proxy_asset_id if served is not None else None
        if run.serving[key] == _UNKNOWN:
            return None
        return run.serving[key] == proxy.id

    # ------------------------------------------------------------------ items
    def _add(
        self,
        run: _Run,
        relationship_type: RelationshipType,
        subject: MediaAssetDto,
        other: MediaAssetDto,
        evidence: list[RelationshipEvidence],
        serving: bool | None = None,
    ) -> None:
        """``subject`` (the duplicate, proxy or derivative) points at ``other`` (its original)."""
        relationship_id = RelationshipId.of(relationship_type, subject.id, other.id).value
        run.items[relationship_id] = RelationshipDto(
            relationship_id=relationship_id,
            type=relationship_type,
            source_asset_id=subject.id,
            target_asset_id=other.id,
            symmetric=False,
            status=None,
            grade=Grade.RECORDED,
            origin=origin_of(relationship_type),
            evidence=tuple(evidence_dto(e) for e in evidence),
            source_interval=None,
            target_interval=None,
            offset=None,
            basis_detector=BASIS_DETECTOR,
            basis_version=LINEAGE_PROJECTION_VERSION,
            contradicted=False,
            outdated_since=None,
            endpoint_states=endpoint_states([subject.id, other.id], [subject, other]),
            history=(),
            created_at=subject.created_at,
            updated_at=subject.updated_at,
            stored=False,
            serving=serving,
        )


def _visible(counterpart: MediaAssetDto, include_archived: bool) -> bool:
    return include_archived or counterpart.status is AssetStatus.ACTIVE


def _evidence(
    kind: EvidenceKind,
    provider: str,
    asset: MediaAssetDto,
    detail: dict[str, JsonValue],
) -> RelationshipEvidence:
    # observed when the library recorded the asset, so the same query gives the same answer
    return RelationshipEvidence(
        kind=kind,
        provider=provider,
        provider_version=LINEAGE_PROJECTION_VERSION,
        observed_at=asset.created_at,
        detail=detail,
    )


def _checksum_evidence(duplicate: MediaAssetDto) -> RelationshipEvidence:
    return _evidence(
        EvidenceKind.CHECKSUM_MATCH, LIBRARY, duplicate, {"sha256": duplicate.checksum}
    )


def _derivation_evidence(derivative: MediaAssetDto) -> RelationshipEvidence:
    derivation = derivative.derivation
    if derivation is None:
        raise ValueError("a derivative has a derivation")
    return _evidence(
        EvidenceKind.LIBRARY_DERIVATION,
        LIBRARY,
        derivative,
        {
            "operation": derivation.operation,
            "version": derivation.version,
            "fingerprint": derivation.fingerprint,
        },
    )


def _provenance_evidence(proxy: MediaAssetDto, provenance: ProxyProvenance) -> RelationshipEvidence:
    return _evidence(
        EvidenceKind.PROXY_PROVENANCE,
        PROXIES,
        proxy,
        {
            "profile": provenance.profile,
            "verdict": provenance.verdict.value,
            "evidence": provenance.evidence.value,
            "spec_fingerprint": provenance.spec_fingerprint,
        },
    )


__all__ = ["LineageProjection", "LineageSettings", "LineageSkip", "ProjectedLineage"]
