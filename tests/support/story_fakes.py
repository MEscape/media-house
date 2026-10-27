"""Doubles and builders for the story-architecture tests (no model, no media, no real library)."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from media_house.modules.asset_relationships.application.contracts import (
    EvidenceDto,
    EvidenceKind,
    Grade,
    RelationshipDto,
    RelationshipOrigin,
    RelationshipPage,
    RelationshipQuery,
    RelationshipStatus,
    RelationshipType,
)
from media_house.modules.audio_visual_alignment.application.contracts import AlignmentDocument
from media_house.modules.content_intelligence.application.analyze_content import (
    AnalyzeContentCommand,
    ContentAnalysisResult,
)
from media_house.modules.content_intelligence.application.contracts import ContentAnalysis
from media_house.modules.media_library.application.contracts import JsonValue, MediaAssetDto
from media_house.modules.media_library.application.dto import DerivationDto, DerivedAssetResultDto
from media_house.modules.story_architecture.application.views import content_facts
from media_house.modules.story_architecture.domain.engine import PlanMeta, plan_story
from media_house.modules.story_architecture.domain.facts import (
    AssetFacts,
    AssetLinkFacts,
    ItemFacts,
    RelationFacts,
    SegmentFacts,
    TopicFacts,
)
from media_house.modules.story_architecture.domain.model import StoryPlan
from media_house.modules.story_architecture.domain.profile import StoryConfig
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Err, NotFoundError, Ok, Result, ValidationError
from tests.support.asset_dto import make_asset
from tests.support.ci_texts import analysis_of, lines_to_transcript

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
META = PlanMeta("sp_test", 1, None, None, NOW, "cfg")


class StoryLibrary:
    """The part of the library the planner uses, with derived documents kept on disk."""

    def __init__(self, root: Path, source_ids: Sequence[str] = ("a1",)) -> None:
        self._root = root
        self._sources = {i: make_asset(i, display_name=f"clip {i}") for i in source_ids}
        self.assets: dict[tuple[str, str], MediaAssetDto] = {}
        self.files: dict[str, Path] = {}
        self.registered = 0

    def get(self, asset_id: str) -> Ok[MediaAssetDto] | Err[NotFoundError]:
        asset = self._sources.get(asset_id)
        return Ok(asset) if asset else Err(NotFoundError("gone", user_message="Not in library."))

    def fingerprint(
        self,
        source_asset_id: str,
        operation: str,
        config: Mapping[str, JsonValue],
        version: int = 1,
    ) -> Ok[str]:
        text = json.dumps([source_asset_id, operation, config, version], sort_keys=True)
        return Ok(hashlib.sha256(text.encode()).hexdigest())

    def find_derived_asset(self, source_asset_id: str, fingerprint: str) -> MediaAssetDto | None:
        return self.assets.get((source_asset_id, fingerprint))

    def local_path(self, asset_id: str) -> Ok[Path] | Err[NotFoundError]:
        path = self.files.get(asset_id)
        return Ok(path) if path is not None and path.exists() else Err(NotFoundError(asset_id))

    def register_derived(
        self,
        source_asset_id: str,
        path: Path,
        *,
        operation: str,
        config: Mapping[str, JsonValue],
        version: int = 1,
        display_name: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> Ok[DerivedAssetResultDto] | Err[ValidationError]:
        fingerprint = self.fingerprint(source_asset_id, operation, config, version).value
        existing = self.assets.get((source_asset_id, fingerprint))
        if existing is not None:
            return Ok(DerivedAssetResultDto(existing, created=False))
        self.registered += 1
        asset_id = f"derived-{self.registered:03d}"
        stored = self._root / f"{asset_id}.json"
        stored.write_bytes(path.read_bytes())
        self.files[asset_id] = stored
        self.assets[(source_asset_id, fingerprint)] = replace(
            make_asset(asset_id),
            display_name=display_name or "",
            metadata=dict(metadata or {}),
            created_at=NOW + timedelta(minutes=self.registered),
            derivation=DerivationDto(source_asset_id, operation, version, dict(config), "f"),
        )
        return Ok(DerivedAssetResultDto(self.assets[(source_asset_id, fingerprint)], created=True))

    def list_derived(self, source_asset_id: str) -> list[MediaAssetDto]:
        return [a for (src, _), a in self.assets.items() if src == source_asset_id]

    def delete(self, asset_id: str, *, include_derived: bool = True) -> Ok[None]:
        self.assets = {k: v for k, v in self.assets.items() if v.id != asset_id}
        return Ok(None)


class FakeContentCatalog:
    def __init__(self, stored: Mapping[str, ContentAnalysis] | None = None) -> None:
        self.stored = dict(stored or {})

    def latest(self, asset_id: str) -> Ok[tuple[ContentAnalysis, MediaAssetDto] | None]:
        found = self.stored.get(asset_id)
        return Ok(None if found is None else (found, make_asset(f"content-{asset_id}")))


class FakeContentAnalyzer:
    """Stands in for the owner: counts requests and answers with a canned analysis."""

    def __init__(self, answer: ContentAnalysis | None = None) -> None:
        self.answer = answer
        self.commands: list[AnalyzeContentCommand] = []

    def execute(
        self, command: AnalyzeContentCommand, ctx: JobContext
    ) -> Ok[ContentAnalysisResult] | Err[ValidationError]:
        self.commands.append(command)
        if self.answer is None:
            return Err(ValidationError("no transcript"))
        return Ok(ContentAnalysisResult(self.answer, None, created=True))


class FakeAlignmentCatalog:
    def __init__(self, stored: Mapping[str, AlignmentDocument] | None = None) -> None:
        self.stored = dict(stored or {})

    def latest(self, asset_id: str) -> Ok[tuple[AlignmentDocument, MediaAssetDto] | None]:
        found = self.stored.get(asset_id)
        return Ok(None if found is None else (found, make_asset(f"align-{asset_id}")))


class FakeGenerator:
    """A text generator that answers from a function and records every prompt."""

    def __init__(self, reply: str = "{}", unavailable: str | None = None) -> None:
        self.reply = reply
        self._unavailable = unavailable
        self.prompts: list[str] = []

    def identity(self) -> str:
        return "fake/1/story-model"

    def unavailable(self) -> str | None:
        return self._unavailable

    def generate(self, system: str, user: str, cancellation: CancellationToken) -> str:
        self.prompts.append(user)
        return self.reply


# --- builders --------------------------------------------------------------------------------
def analysis_for(lines: Sequence[str], asset_id: str = "a1") -> ContentAnalysis:
    return analysis_of(lines_to_transcript(lines, asset_id=asset_id))


def facts_for(lines: Sequence[str], asset_id: str = "a1") -> AssetFacts:
    return content_facts(analysis_for(lines, asset_id), asset_id, f"clip {asset_id}")


def seg(
    n: int,
    text: str = "",
    *,
    roles: Sequence[tuple[str, float | None]] = (),
    start: float | None = None,
    seconds: float = 10.0,
    timed: bool = True,
    **scores: object,
) -> SegmentFacts:
    """A hand-built passage ``s0000n`` that lasts ``seconds`` from ``start`` (default n*10)."""
    begin = (n - 1) * 10.0 if start is None else start
    body = text or f"Statement number {n} about the topic."
    fields: dict[str, object] = {
        "importance": 0.5,
        "density": 0.4,
        "standalone": 0.7,
        "hook": 0.2,
        "removal": 0.1,
    } | scores
    return SegmentFacts(
        segment_id=f"s{n:05d}",
        unit_ids=(f"u{n:06d}",),
        topic_ids=(),
        start=begin if timed else None,
        end=begin + seconds if timed else None,
        exact_time=timed,
        text=body,
        words=len(body.split()),
        roles=tuple(roles),
        **fields,  # type: ignore[arg-type]
    )


def topic(n: int, *segments: int, label: str = "", level: str = "section") -> TopicFacts:
    return TopicFacts(
        topic_id=f"t{n:04d}",
        level=level,
        label=label or f"topic {n}",
        segment_ids=tuple(f"s{i:05d}" for i in segments),
    )


def rel(kind: str, source: int, target: int, adds: str | None = None) -> RelationFacts:
    return RelationFacts(
        rel_id=f"r{source}{target}{kind[:2]}",
        kind=kind,
        source_segment=f"s{source:05d}",
        target_segment=f"s{target:05d}",
        adds=adds,
    )


def item(item_id: str, kind: str, segment: int, text: str = "", **extra: object) -> ItemFacts:
    return ItemFacts(
        item_id=item_id,
        kind=kind,
        text=text or f"{kind} in segment {segment}",
        origin="direct",
        segment_id=f"s{segment:05d}",
        unit_ids=(f"u{segment:06d}",),
        **extra,  # type: ignore[arg-type]
    )


def asset(
    segments: Sequence[SegmentFacts],
    topics: Sequence[TopicFacts],
    *,
    asset_id: str = "a1",
    relations: Sequence[RelationFacts] = (),
    items: Sequence[ItemFacts] = (),
    **extra: object,
) -> AssetFacts:
    return AssetFacts(
        asset_id=asset_id,
        name=f"clip {asset_id}",
        language="en",
        duration=None,
        state="ok",
        analysis_identity=f"id-{asset_id}",
        segments=tuple(segments),
        topics=tuple(topics),
        relations=tuple(relations),
        items=tuple(items),
        **extra,  # type: ignore[arg-type]
    )


def plan_of(
    facts: AssetFacts | Sequence[AssetFacts],
    config: StoryConfig | None = None,
    links: Sequence[AssetLinkFacts] = (),
) -> StoryPlan:
    assets = (facts,) if isinstance(facts, AssetFacts) else tuple(facts)
    return plan_story(assets, config or StoryConfig(), META, tuple(links))


def link(
    relation: str,
    source: str,
    target: str,
    *,
    status: str | None = "proposed",
    grade: str = "suggestive",
    contradicted: bool = False,
    outdated: bool = False,
    relationship_id: str = "",
) -> AssetLinkFacts:
    """A relation between two recordings as the planner reads it."""
    rid = relationship_id or f"{relation}-{source}-{target}"
    return AssetLinkFacts(
        relationship_id=rid,
        relation=relation,
        source_asset=source,
        target_asset=target,
        status=status,
        grade=grade,
        contradicted=contradicted,
        outdated=outdated,
        evidence=("production_tag_match (production_tag_takes 1)",),
        revision=f"{rid}|{status}|{grade}",
    )


def relationship_dto(
    relation: RelationshipType,
    source: str,
    target: str,
    *,
    status: RelationshipStatus | None = RelationshipStatus.PROPOSED,
    grade: Grade = Grade.SUGGESTIVE,
    revision: int = 0,
) -> RelationshipDto:
    """What ``RelationshipCatalog`` answers; ``revision`` moves ``updated_at`` like a refresh."""
    stored = status is not None
    return RelationshipDto(
        relationship_id=f"{relation.value}-{source}-{target}",
        type=relation,
        source_asset_id=source,
        target_asset_id=target,
        symmetric=stored,
        status=status,
        grade=grade,
        origin=RelationshipOrigin.DETECTOR if stored else RelationshipOrigin.LIBRARY_RECORD,
        evidence=(
            EvidenceDto(
                EvidenceKind.PRODUCTION_TAG_MATCH if stored else EvidenceKind.CHECKSUM_MATCH,
                "production_tag_takes" if stored else "media_library",
                "1",
                NOW,
                {},
                None,
                False,
            ),
        ),
        source_interval=None,
        target_interval=None,
        offset=None,
        basis_detector="production_tag_takes" if stored else "library",
        basis_version="1",
        contradicted=False,
        outdated_since=None,
        endpoint_states={},
        history=(),
        created_at=NOW,
        updated_at=NOW + timedelta(minutes=revision),
        stored=stored,
    )


class FakeRelationshipCatalog:
    """``RelationshipCatalog`` over a list; counts calls and can fail like a broken store."""

    def __init__(self, *items: RelationshipDto) -> None:
        self.items = list(items)
        self.calls: list[str] = []
        self.fail = False

    def relationships_of(
        self, asset_id: str, query: RelationshipQuery | None = None
    ) -> Result[RelationshipPage, ValidationError | NotFoundError]:
        self.calls.append(asset_id)
        if self.fail:
            return Err(NotFoundError("gone", user_message="The relationship store is unavailable."))
        mine = tuple(i for i in self.items if asset_id in (i.source_asset_id, i.target_asset_id))
        return Ok(RelationshipPage(mine))

    def get(self, relationship_id: str) -> Result[RelationshipDto, NotFoundError]:
        return Err(NotFoundError(relationship_id))
