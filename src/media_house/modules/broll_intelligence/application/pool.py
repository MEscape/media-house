"""The footage that can be searched: every usable video of the library, as shot facts.

The library is walked ONCE per run (paged, capped), each asset contributes its shot index (a small
derived document, never the full analysis) and its relationships, and an asset that cannot be
searched is listed with the reason instead of vanishing: "nothing suitable" must be told apart from
"nothing was looked at". Evidence found on a proxy or improved version is attributed to the
ORIGINAL asset, because that is the id every consumer must use.

Embeddings are comparable only within one model: shots embedded by another model than the one that
encodes the passages keep their words and lose their vectors, and the result says so.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field

from media_house.modules.asset_relationships.application.contracts import (
    RelationshipCatalog,
    RelationshipQuery,
    RelationshipType,
)
from media_house.modules.broll_intelligence.application.views import (
    digest,
    relation_facts,
    shot_facts,
)
from media_house.modules.broll_intelligence.domain.facts import (
    PoolFact,
    RelationFact,
    ShotFact,
)
from media_house.modules.broll_intelligence.domain.model import InputRecord
from media_house.modules.broll_intelligence.domain.values import AssetVia, InputHow
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    MediaAssetDto,
    MediaLibrary,
    MediaQuery,
    MediaType,
)
from media_house.modules.video_intelligence.application.contracts import (
    IndexedAsset,
    ShotIndexCatalog,
)
from media_house.shared.errors import Err
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

_PAGE = 200
_RELATION_TYPES = frozenset(
    {
        RelationshipType.ALTERNATE_TAKE_OF,
        RelationshipType.TEMPORALLY_OVERLAPS_WITH,
        RelationshipType.EXACT_DUPLICATE_OF,
    }
)


@dataclass(frozen=True, slots=True)
class LibraryScope:
    """Which footage may be searched. Empty means no restriction."""

    #: Only these assets (library ids).
    asset_ids: tuple[str, ...] = ()
    #: Assets that belong to ANY of these groups / carry ALL of these tags.
    group_ids: frozenset[str] = frozenset()
    tags: frozenset[str] = frozenset()
    exclude: frozenset[str] = frozenset()
    #: At most this many assets are searched; the result says when the library was larger.
    max_assets: int = 500
    #: Other shots of the passages' own recordings may serve as cutaways.
    include_targets: bool = True


@dataclass(slots=True)
class PoolBuild:
    shots: list[ShotFact] = field(default_factory=list)
    #: The base64 half-precision embedding of each shot, aligned with ``shots`` ("" = none).
    packed: list[str] = field(default_factory=list)
    dimension: int = 0
    embedding_model: str | None = None
    fact: PoolFact = field(
        default_factory=lambda: PoolFact(assets=0, with_shots=0, shots=0, shots_with_embedding=0)
    )
    identity: str = ""
    records: list[InputRecord] = field(default_factory=list)
    #: Assets whose embeddings were left out because another model made them.
    other_model: list[str] = field(default_factory=list)


class LibraryPool:
    def __init__(
        self,
        library: MediaLibrary,
        indexes: ShotIndexCatalog,
        relationships: RelationshipCatalog,
    ) -> None:
        self._library = library
        self._indexes = indexes
        self._relationships = relationships

    def build(
        self,
        scope: LibraryScope,
        target_assets: frozenset[str],
        supplied: Mapping[str, IndexedAsset],
        encoder_model: str | None,
    ) -> PoolBuild:
        assets, limits = self._assets(scope, target_assets)
        found: list[tuple[MediaAssetDto, IndexedAsset, MediaAssetDto, AssetVia]] = []
        skipped: list[tuple[str, str]] = []
        relations: dict[str, tuple[RelationFact, ...]] = {}
        derived: dict[str, str] = {}
        out = PoolBuild()
        for asset in assets:
            located = self._index_of(asset, supplied)
            if isinstance(located, str):
                skipped.append((asset.id, located))
                continue
            analysed, indexed, via = located
            if not indexed.index.shots:
                skipped.append((asset.id, "its video analysis has no shots"))
                continue
            found.append((asset, indexed, analysed, via))
            relations[asset.id] = self._relations(asset.id)
            derived[asset.id] = ids_of(self._library.list_derived(asset.id))
            out.records.append(
                InputRecord(
                    kind="shot_index",
                    how=InputHow.SUPPLIED if asset.id in supplied else InputHow.REUSED,
                    asset_id=asset.id,
                    identity=indexed.index.analysis_identity,
                    detail=indexed.asset.id,
                )
            )
        model = encoder_model or _common_model(found)
        for asset, indexed, analysed, via in found:
            facts = shot_facts(indexed, analysed, asset, via)
            usable = (
                indexed.index.embedding_model == model and indexed.index.embedding_dimension > 0
            )
            if usable:
                out.dimension = indexed.index.embedding_dimension
            elif indexed.index.embedding_model is not None:
                out.other_model.append(asset.id)
            rels = relations[asset.id]
            for fact, row in zip(facts, indexed.index.shots, strict=True):
                out.shots.append(_with_relations(fact, rels))
                out.packed.append(row.embedding_f16 if usable else "")
        out.embedding_model = model if out.dimension else None
        embedded = sum(1 for p in out.packed if p)
        if out.other_model:
            limits.append(
                f"{len(out.other_model)} recording(s) were embedded by another model than "
                f"{model}; their pictures are searched by words only"
            )
        out.fact = PoolFact(
            assets=len(assets),
            with_shots=len(found),
            shots=len(out.shots),
            shots_with_embedding=embedded,
            skipped=tuple(skipped),
            limits=tuple(limits),
        )
        out.identity = digest(
            *sorted(
                f"{a.id}:{i.asset.id}:{i.index.analysis_identity}:{i.index.index_version}:"
                f"{_rel(relations[a.id])}:{derived[a.id]}"
                for a, i, _, _ in found
            ),
            model,
            len(skipped),
        )
        _log.info(
            "Footage pool built",
            assets=len(assets),
            searchable=len(found),
            shots=len(out.shots),
            embedded=embedded,
        )
        return out

    # --- which assets ----------------------------------------------------------------------
    def _assets(
        self, scope: LibraryScope, target_assets: frozenset[str]
    ) -> tuple[list[MediaAssetDto], list[str]]:
        limits: list[str] = []
        excluded = set(scope.exclude)
        if not scope.include_targets:
            excluded |= target_assets
        if scope.asset_ids:
            found = [
                a for a in self._library.get_many(list(scope.asset_ids)) if a.id not in excluded
            ]
            return [a for a in found if self._searchable(a)], limits
        collected: list[MediaAssetDto] = []
        page = 1
        while True:
            result = self._library.search(
                MediaQuery(
                    media_types=frozenset({MediaType.VIDEO}),
                    roles=frozenset({AssetRole.ORIGINAL}),
                    group_ids=scope.group_ids,
                    tags=scope.tags,
                    page=page,
                    page_size=_PAGE,
                )
            )
            collected += [a for a in result.items if a.id not in excluded and self._searchable(a)]
            if len(collected) >= scope.max_assets:
                if result.has_next or len(collected) > scope.max_assets:
                    limits.append(
                        f"only the first {scope.max_assets} recordings of the library were searched"
                    )
                return collected[: scope.max_assets], limits
            if not result.has_next:
                return collected, limits
            page += 1

    def _searchable(self, asset: MediaAssetDto) -> bool:
        return asset.media_type is MediaType.VIDEO and asset.role is AssetRole.ORIGINAL

    def _index_of(
        self, asset: MediaAssetDto, supplied: Mapping[str, IndexedAsset]
    ) -> tuple[MediaAssetDto, IndexedAsset, AssetVia] | str:
        if isinstance(self._library.local_path(asset.id), Err):
            return "the file is not available in the library"
        if asset.id in supplied:
            return asset, supplied[asset.id], AssetVia.ORIGINAL
        own = self._indexes.shot_index(asset.id)
        if isinstance(own, Err):
            return f"its library record could not be read: {own.error.user_message}"
        if own.value is not None:
            return asset, own.value, AssetVia.ORIGINAL
        for derived in sorted(
            (
                d
                for d in self._library.list_derived(asset.id)
                if d.media_type is MediaType.VIDEO and d.derivation is not None
            ),
            key=lambda d: (d.created_at, d.id),
            reverse=True,
        ):
            via_derived = self._indexes.shot_index(derived.id)
            if not isinstance(via_derived, Err) and via_derived.value is not None:
                operation = derived.derivation.operation if derived.derivation else ""
                return (
                    derived,
                    via_derived.value,
                    (AssetVia.PROXY if operation == "proxy" else AssetVia.DERIVED),
                )
        return "no video analysis is stored for it"

    def _relations(self, asset_id: str) -> tuple[RelationFact, ...]:
        page = self._relationships.relationships_of(
            asset_id, RelationshipQuery(types=_RELATION_TYPES, page_size=50)
        )
        if isinstance(page, Err):
            return ()
        return relation_facts(page.value, asset_id)


def _common_model(
    found: list[tuple[MediaAssetDto, IndexedAsset, MediaAssetDto, AssetVia]],
) -> str | None:
    models = Counter(i.index.embedding_model for _, i, _, _ in found if i.index.embedding_model)
    return models.most_common(1)[0][0] if models else None


def _with_relations(shot: ShotFact, relations: tuple[RelationFact, ...]) -> ShotFact:
    from dataclasses import replace

    return replace(shot, relations=relations) if relations else shot


def _rel(relations: tuple[RelationFact, ...]) -> str:
    return ",".join(sorted(f"{r.other_asset_id}/{r.kind}/{r.certainty}" for r in relations))


#: The ``processing_type`` of the stored analyses of a recording that enrich its footage: its sound
#: (``audio_intelligence``) and its sync/clock (``audio_visual_alignment``). Pinned to the owners'
#: names by a test; B-roll's own output and the downstream plans are deliberately NOT watched, or
#: every result would change the key of the next run.
WATCHED_ANALYSES = frozenset({"audio_intelligence", "audio_visual_alignment"})


def ids_of(derived: list[MediaAssetDto]) -> str:
    """The ids of the watched analyses of an asset: a new analysis of its sound or sync changes
    the pool's identity even though the shot index may not."""
    return digest(
        *sorted(d.id for d in derived if d.metadata.get("processing_type") in WATCHED_ANALYSES)
    )
