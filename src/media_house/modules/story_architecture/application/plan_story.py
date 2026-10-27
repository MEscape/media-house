"""Use case: stored content analyses (and alignments) -> one story plan, stored in the library.

Nothing is transcribed, analysed or aligned here. The content analysis of each recording is READ
through ``ContentCatalog`` (``Acquire.REQUEST`` lets its owner produce a missing one); the alignment
is read when it exists and the plan is simply less specific about shots when it does not. The plan
is one derived library document, keyed by the identity of every input actually used, the
configuration, the model (if one reworded anything) and the versions, so any change misses and
nothing else does. Earlier plans of the same sources and format are kept: the new one records which
it follows.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from media_house.core.application.ports import Clock
from media_house.modules.asset_relationships.application.contracts import (
    RelationshipCatalog,
    RelationshipDto,
    RelationshipQuery,
)
from media_house.modules.audio_visual_alignment.application.contracts import (
    AlignmentCatalog,
    AlignmentDocument,
)
from media_house.modules.content_intelligence.application.contracts import (
    AnalyzeContentCommand,
    ContentAnalysis,
    ContentAnalyzer,
    ContentCatalog,
    TextGenerator,
)
from media_house.modules.content_intelligence.application.contracts import (
    ResultState as ContentState,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.story_architecture.application.views import content_facts, link_facts
from media_house.modules.story_architecture.domain.engine import PlanMeta, plan_story
from media_house.modules.story_architecture.domain.errors import (
    InvalidStoryConfig,
    InvalidStoryPlan,
    MissingAnalysis,
    NothingToPlan,
    StoryArchitectureError,
)
from media_house.modules.story_architecture.domain.facts import AssetFacts, AssetLinkFacts
from media_house.modules.story_architecture.domain.model import (
    InputRecord,
    Provenance,
    StageReport,
    StoryPlan,
)
from media_house.modules.story_architecture.domain.profile import StoryConfig
from media_house.modules.story_architecture.domain.refine import (
    PROMPT_VERSION,
    apply_reply,
    build_requests,
)
from media_house.modules.story_architecture.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.story_architecture.domain.validation import validate_plan
from media_house.modules.story_architecture.domain.values import (
    PLANNER_VERSION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    StageState,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, MediaHouseError, Ok, Result, ValidationError
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type PlanStoryError = MediaError | StoryArchitectureError | MediaHouseError


class Acquire(StrEnum):
    #: Read the stored content analysis; fail with ``MissingAnalysis`` if there is none.
    STORED = "stored"
    #: Ask its owner to produce a missing content analysis (this may transcribe: slow).
    REQUEST = "request"


@dataclass(frozen=True, slots=True)
class PlanStoryCommand:
    #: Library assets, in the order the story should follow. One asset is the usual case.
    asset_ids: tuple[str, ...]
    config: StoryConfig = field(default_factory=StoryConfig)
    #: A label the caller keeps for the project; stored on the plan, never interpreted.
    project_id: str | None = None
    acquire: Acquire = Acquire.STORED
    #: Read stored alignments to attach shots to visual opportunities.
    use_alignment: bool = True
    #: Let a language model reword section purposes and the main message (grounded, checked).
    llm: bool = False
    #: For a story of several recordings: read how they relate (copies, takes, overlaps) from
    #: ``asset_relationships``. Never requested, never required; a missing answer is recorded.
    use_relationships: bool = True
    #: Results the caller already holds (used as given, nothing is looked up for them).
    content_analyses: Mapping[str, ContentAnalysis] = field(default_factory=dict)
    alignments: Mapping[str, AlignmentDocument] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoryResult:
    plan: StoryPlan
    #: The stored document (a derived asset of the first source).
    asset: MediaAssetDto
    #: ``False`` when the identical plan already existed and nothing was computed.
    created: bool


@dataclass(slots=True)
class _Gathered:
    facts: list[AssetFacts] = field(default_factory=list)
    records: list[InputRecord] = field(default_factory=list)
    identities: dict[str, JsonValue] = field(default_factory=dict)
    known: dict[str, frozenset[str]] = field(default_factory=dict)
    links: list[AssetLinkFacts] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class PlanStory:
    def __init__(
        self,
        library: MediaLibrary,
        content: ContentCatalog,
        analyzer: ContentAnalyzer,
        alignments: AlignmentCatalog,
        paths: AppPaths,
        clock: Clock,
        generator: TextGenerator | None = None,
        relationships: RelationshipCatalog | None = None,
    ) -> None:
        self._library = library
        self._content = content
        self._analyzer = analyzer
        self._alignments = alignments
        self._clock = clock
        self._generator = generator
        self._relationships = relationships
        self._documents = DerivedDocuments(library, paths)

    def execute(
        self, command: PlanStoryCommand, ctx: JobContext
    ) -> Result[StoryResult, PlanStoryError]:
        try:
            profile = command.config.resolve()
        except InvalidStoryConfig as exc:
            return Err(exc)
        if not command.asset_ids or len(set(command.asset_ids)) != len(command.asset_ids):
            return Err(
                ValidationError(
                    "asset_ids must be non-empty and unique",
                    field="asset_ids",
                    user_message="Give one or more different recordings to plan a story for.",
                )
            )
        ctx.progress.report(1, _STEPS, "Reading the stored analyses")
        gathered = self._gather(command, ctx)
        if isinstance(gathered, Err):
            return gathered

        llm_id = self._llm_identity(command)
        key = self._cache_key(command, gathered.value, llm_id)
        anchor = command.asset_ids[0]
        spec = DocumentSpec(RESULT_OPERATION, PLANNER_VERSION, key)
        fingerprint = self._documents.fingerprint(anchor, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        cached = self._documents.load(anchor, fingerprint.value, plan_from_json)
        if cached is not None:
            _log.info("Story plan reused", asset_id=cached[1].id, cache_hit=True)
            return Ok(StoryResult(cached[0], cached[1], created=False))

        ctx.raise_if_cancelled()
        ctx.progress.report(2, _STEPS, "Planning the story")
        anchor_asset = self._library.get(anchor)
        if isinstance(anchor_asset, Err):
            return anchor_asset
        version, supersedes = self._history(anchor, profile.name, command.asset_ids)
        digest = _digest(key)
        meta = PlanMeta(
            story_plan_id=f"sp_{digest[:16]}",
            plan_version=version,
            supersedes=supersedes,
            project_id=command.project_id,
            created_at=self._clock.now(),
            configuration_fingerprint=digest,
            inputs=tuple(gathered.value.records),
            warnings=tuple(gathered.value.warnings),
        )
        try:
            plan = plan_story(
                tuple(gathered.value.facts),
                command.config,
                meta,
                tuple(gathered.value.links),
            )
        except NothingToPlan as exc:
            return Err(exc)
        if command.llm:
            plan = self._refine(plan, tuple(gathered.value.facts), llm_id, ctx)
        problems = validate_plan(plan, gathered.value.known)
        if problems:
            _log.error("Story plan rejected its own result", problems=problems[:5])
            return Err(InvalidStoryPlan(problems))

        ctx.progress.report(_STEPS - 1, _STEPS, "Saving")
        stored = self._documents.store(
            anchor_asset.value,
            plan_to_json(plan),
            filename="story_plan.json",
            spec=spec,
            display_name=f"{anchor_asset.value.display_name} (story plan)",
            metadata={
                "format": profile.name,
                "assets": ",".join(command.asset_ids),
                "plan_version": plan.plan_version,
                "sections": len(plan.sections),
                "issues": len(plan.completeness_issues),
                "state": plan.state.value,
            },
        )
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Story plan created",
            asset_id=stored.value.id,
            sections=len(plan.sections),
            beats=len(plan.beats),
            issues=len(plan.completeness_issues),
            plan_version=plan.plan_version,
        )
        return Ok(StoryResult(plan, stored.value, created=True))

    # --- inputs ---------------------------------------------------------------------------
    def _gather(
        self, command: PlanStoryCommand, ctx: JobContext
    ) -> Result[_Gathered, PlanStoryError]:
        out = _Gathered()
        for asset_id in command.asset_ids:
            found = self._library.get(asset_id)
            if isinstance(found, Err):
                return found
            asset = found.value
            analysis = self._analysis(command, asset_id, ctx, out)
            if isinstance(analysis, Err):
                return analysis
            if analysis.value.state is ContentState.NOT_APPLICABLE:
                return Err(NothingToPlan(f"{asset.display_name} has no analysed speech"))
            alignment = self._alignment(command, asset_id, out)
            facts = content_facts(analysis.value, asset_id, asset.display_name, alignment)
            out.facts.append(facts)
            out.known[asset_id] = frozenset(s.segment_id for s in facts.segments)
            out.identities[asset_id] = {
                "content": facts.analysis_identity,
                "alignment": facts.alignment_identity,
            }
        if command.use_relationships and len(command.asset_ids) > 1:
            self._related(command.asset_ids, out)
        return Ok(out)

    def _related(self, asset_ids: tuple[str, ...], out: _Gathered) -> None:
        """How the story's recordings relate. A catalog that cannot answer is recorded, not fatal:
        the plan is simply made without relations and says so."""
        if self._relationships is None:
            return
        wanted, found = set(asset_ids), {}
        for asset_id in asset_ids:
            cursor: str | None = None
            for _ in range(_MAX_RELATION_PAGES):
                page = self._relationships.relationships_of(
                    asset_id,
                    RelationshipQuery(cursor=cursor, page_size=RelationshipQuery.MAX_PAGE_SIZE),
                )
                if isinstance(page, Err):
                    out.records.append(
                        InputRecord(
                            kind="asset_relationships",
                            how="not_available",
                            asset_id=asset_id,
                            detail=page.error.user_message,
                        )
                    )
                    out.warnings.append(f"relations of {asset_id} could not be read")
                    break
                for dto in page.value.items:
                    if {dto.source_asset_id, dto.target_asset_id} <= wanted:
                        found[dto.relationship_id] = dto
                cursor = page.value.next_cursor
                if cursor is None:
                    break
        out.links = [link_facts(d) for d in sorted(found.values(), key=_by_id)]
        out.records.append(
            InputRecord(
                kind="asset_relationships",
                how="reused",
                detail=f"{len(out.links)} relations among the recordings",
            )
        )

    def _analysis(
        self, command: PlanStoryCommand, asset_id: str, ctx: JobContext, out: _Gathered
    ) -> Result[ContentAnalysis, PlanStoryError]:
        supplied = command.content_analyses.get(asset_id)
        if supplied is not None:
            out.records.append(
                InputRecord(kind="content_analysis", how="supplied", asset_id=asset_id)
            )
            return Ok(supplied)
        stored = self._content.latest(asset_id)
        if isinstance(stored, Err):
            return stored
        if stored.value is not None:
            out.records.append(
                InputRecord(
                    kind="content_analysis",
                    how="reused",
                    asset_id=asset_id,
                    detail=stored.value[1].id,
                )
            )
            return Ok(stored.value[0])
        if command.acquire is not Acquire.REQUEST:
            return Err(MissingAnalysis(asset_id))
        requested = self._analyzer.execute(AnalyzeContentCommand(source_asset_id=asset_id), ctx)
        if isinstance(requested, Err):
            return requested
        out.records.append(InputRecord(kind="content_analysis", how="requested", asset_id=asset_id))
        return Ok(requested.value.analysis)

    def _alignment(
        self, command: PlanStoryCommand, asset_id: str, out: _Gathered
    ) -> AlignmentDocument | None:
        if not command.use_alignment:
            return None
        supplied = command.alignments.get(asset_id)
        if supplied is not None:
            out.records.append(InputRecord(kind="alignment", how="supplied", asset_id=asset_id))
            return supplied
        stored = self._alignments.latest(asset_id)
        if isinstance(stored, Ok) and stored.value is not None:
            out.records.append(
                InputRecord(
                    kind="alignment", how="reused", asset_id=asset_id, detail=stored.value[1].id
                )
            )
            return stored.value[0]
        detail = (
            "no stored alignment"
            if isinstance(stored, Ok)
            else f"the library could not be read: {stored.error.user_message}"
        )
        out.records.append(
            InputRecord(kind="alignment", how="not_available", asset_id=asset_id, detail=detail)
        )
        return None

    # --- identity -------------------------------------------------------------------------
    def _llm_identity(self, command: PlanStoryCommand) -> str | None:
        """The model's identity if one will really run, else ``None`` (the plan is then keyed and
        labelled as the deterministic one, never as a model's)."""
        if not command.llm or self._generator is None:
            return None
        return None if self._generator.unavailable() is not None else self._generator.identity()

    @staticmethod
    def _cache_key(
        command: PlanStoryCommand, gathered: _Gathered, llm_id: str | None
    ) -> Mapping[str, JsonValue]:
        """Everything that can change the plan and nothing that cannot (not time, job or device)."""
        return {
            "assets": list(command.asset_ids),
            "inputs": dict(gathered.identities),
            "project": command.project_id,
            "relations": [link.revision for link in gathered.links],
            "config": json.loads(json.dumps(command.config.fingerprint())),
            # Asking for a model that cannot run is a different outcome (it carries the stage
            # report saying so) from never asking, so the two are stored apart.
            "llm_requested": command.llm,
            "llm": {"model": llm_id, "prompt": PROMPT_VERSION} if llm_id else None,
            "versions": {
                "planner": PLANNER_VERSION,
                "schema": SCHEMA_VERSION,
                "vocabulary": VOCABULARY_VERSION,
            },
        }

    def _history(self, anchor: str, fmt: str, asset_ids: Sequence[str]) -> tuple[int, str | None]:
        """1 for the first plan of these sources and format; later ones count up and name their
        predecessor. Earlier plans are never changed or removed."""
        wanted = ",".join(asset_ids)
        earlier = [
            a
            for a in self._library.list_derived(anchor)
            if a.metadata.get("processing_type") == RESULT_OPERATION
            and a.metadata.get("format") == fmt
            and a.metadata.get("assets") == wanted
        ]
        if not earlier:
            return 1, None
        newest = max(earlier, key=lambda a: (a.created_at, a.id))
        return len(earlier) + 1, newest.id

    # --- optional model wording -----------------------------------------------------------
    def _refine(
        self,
        plan: StoryPlan,
        facts: tuple[AssetFacts, ...],
        llm_id: str | None,
        ctx: JobContext,
    ) -> StoryPlan:
        if self._generator is None:
            return _with_stage(plan, StageState.NOT_AVAILABLE, "no language model is configured")
        why = self._generator.unavailable()
        if why is not None or llm_id is None:
            _log.info("Language model not available, keeping the deterministic wording", reason=why)
            return _with_stage(plan, StageState.NOT_AVAILABLE, why or "language model unavailable")
        accepted = rejected = calls = 0
        current = plan
        try:
            for request in build_requests(plan, facts):
                ctx.raise_if_cancelled()
                reply = self._generator.generate(request.system, request.user, ctx.cancellation)
                calls += 1
                outcome = apply_reply(current, request, reply, facts)
                current, accepted, rejected = (
                    outcome.plan,
                    accepted + outcome.accepted,
                    rejected + outcome.rejected,
                )
        except MediaHouseError as exc:
            _log.warning("Language model failed, keeping what was reworded", reason=str(exc))
            current = _with_stage(
                current, StageState.FAILED, f"the language model failed: {exc.user_message}"
            )
        else:
            current = _with_stage(
                current,
                StageState.OK,
                f"{accepted} statements reworded, {rejected} rejected by the grounding checks",
            )
        provenance: Provenance = replace(
            current.provenance,
            engines=(("language_model", llm_id),),
            prompt_version=PROMPT_VERSION,
            usage=(
                ("calls", float(calls)),
                ("accepted", float(accepted)),
                ("rejected", float(rejected)),
            ),
        )
        return replace(current, provenance=provenance)


_STEPS = 4
_MAX_RELATION_PAGES = 5


def _by_id(dto: RelationshipDto) -> str:
    return dto.relationship_id


def _with_stage(plan: StoryPlan, state: StageState, detail: str) -> StoryPlan:
    return replace(
        plan,
        stages=(*plan.stages, StageReport(stage="refinement", state=state, detail=detail)),
        warnings=(*plan.warnings, f"refinement: {detail}")
        if state is not StageState.OK
        else plan.warnings,
    )


def _digest(key: Mapping[str, JsonValue]) -> str:
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


__all__ = ["Acquire", "PlanStory", "PlanStoryCommand", "PlanStoryError", "StoryResult"]
