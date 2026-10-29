"""Use case: a proposed sequence + the stored analyses of its recordings -> one continuity analysis.

The input is a ``ProposedSequence`` (the clips of an edit with their source ranges and timeline
places); the caller builds it from the edit plan, so this module never imports the editing stage.
Nothing is analysed, aligned, transcribed or probed here: the picture, sound and sync evidence is
READ through the owners' catalogs, or used as supplied. A missing optional input makes a cut less
decidable (an unresolved item, an evidence record), never invented.

The analysis is one derived library document of the sequence's first recording. A cut whose
fingerprint (ranges, flags, the identity of every analysis it rests on, the configuration and the
versions) is unchanged since the previous analysis is not judged again: its issues are carried over
and its evidence is not even loaded. A person's accept/reject decisions follow their issues.
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from fractions import Fraction
from itertools import pairwise

from media_house.core.application.ports import Clock
from media_house.modules.asset_relationships.application.contracts import (
    RelationshipCatalog,
    RelationshipQuery,
    RelationshipStatus,
    RelationshipType,
)
from media_house.modules.audio_intelligence.application.contracts import (
    AudioCatalog,
    AudioIntelligenceTimeline,
)
from media_house.modules.audio_visual_alignment.application.contracts import (
    AlignmentCatalog,
    AlignmentDocument,
)
from media_house.modules.broll_intelligence.application.contracts import (
    BrollAnalysis,
    BrollCatalog,
)
from media_house.modules.continuity_intelligence.application.results import ContinuityResults
from media_house.modules.continuity_intelligence.application.views import (
    audio_edge,
    digest,
    edge_fact,
    sync_state,
)
from media_house.modules.continuity_intelligence.domain.engine import (
    AnalysisMeta,
    analyse_continuity,
)
from media_house.modules.continuity_intelligence.domain.errors import (
    ContinuityIntelligenceError,
    InvalidContinuityAnalysis,
    InvalidContinuityConfig,
    InvalidSequence,
    NothingToJudge,
)
from media_house.modules.continuity_intelligence.domain.facts import (
    ProposedClip,
    ProposedSequence,
    SideFact,
    TransitionFact,
)
from media_house.modules.continuity_intelligence.domain.model import (
    ContinuityAnalysis,
    InputRecord,
    IssueDecision,
    StageReport,
)
from media_house.modules.continuity_intelligence.domain.profile import ContinuityConfig
from media_house.modules.continuity_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.continuity_intelligence.domain.values import (
    ANALYZER_VERSION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    InputHow,
    Severity,
    StageState,
    TransitionKind,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.video_intelligence.application.contracts import (
    VideoAnalysis,
    VideoCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type AnalyzeContinuityError = MediaError | ContinuityIntelligenceError | MediaHouseError

_STEPS = 5
_EPS = Fraction(1, 1000)
_RELATED = frozenset(
    {RelationshipType.ALTERNATE_TAKE_OF, RelationshipType.TEMPORALLY_OVERLAPS_WITH}
)


@dataclass(frozen=True, slots=True)
class AnalyzeContinuityCommand:
    sequence: ProposedSequence
    config: ContinuityConfig = field(default_factory=ContinuityConfig)
    #: A person's verdicts on findings; those of the previous analysis are carried unless replaced.
    decisions: tuple[IssueDecision, ...] = ()
    carry_decisions: bool = True
    use_video: bool = True
    use_audio: bool = True
    use_alignment: bool = True
    use_broll: bool = True
    #: Results the caller already holds (used as given, nothing is looked up for them).
    video_analyses: Mapping[str, VideoAnalysis] = field(default_factory=dict)
    audio_timelines: Mapping[str, AudioIntelligenceTimeline] = field(default_factory=dict)
    alignments: Mapping[str, AlignmentDocument] = field(default_factory=dict)
    broll: BrollAnalysis | None = None


@dataclass(frozen=True, slots=True)
class ContinuityResult:
    analysis: ContinuityAnalysis
    #: The stored document (a derived asset of the sequence's first recording).
    asset: MediaAssetDto
    #: ``False`` when the identical analysis already existed and nothing was computed.
    created: bool


@dataclass(slots=True)
class _Evidence:
    video: dict[str, VideoAnalysis] = field(default_factory=dict)
    audio: dict[str, AudioIntelligenceTimeline] = field(default_factory=dict)
    alignment: dict[str, AlignmentDocument] = field(default_factory=dict)
    related: dict[str, tuple[str, ...]] = field(default_factory=dict)
    records: list[InputRecord] = field(default_factory=list)
    identities: dict[str, JsonValue] = field(default_factory=dict)


class AnalyzeContinuity:
    def __init__(
        self,
        library: MediaLibrary,
        video: VideoCatalog,
        audio: AudioCatalog,
        alignments: AlignmentCatalog,
        relationships: RelationshipCatalog,
        broll: BrollCatalog,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._video = video
        self._audio = audio
        self._alignments = alignments
        self._relationships = relationships
        self._broll = broll
        self._clock = clock
        self._documents = DerivedDocuments(library, paths)
        self._results = ContinuityResults(library)

    def execute(
        self, command: AnalyzeContinuityCommand, ctx: JobContext
    ) -> Result[ContinuityResult, AnalyzeContinuityError]:
        sequence = command.sequence
        problems = _problems(sequence)
        if problems:
            return Err(InvalidSequence(problems))
        try:
            profile = command.config.resolve(sequence.story_format)
        except InvalidContinuityConfig as exc:
            return Err(exc)
        cuts = _cuts(sequence)
        if not cuts:
            return Err(NothingToJudge())
        anchor = sequence.clips[0].asset_id
        ctx.progress.report(1, _STEPS, "Reading the stored analyses")
        evidence = self._evidence(command, sequence)
        if isinstance(evidence, Err):
            return evidence
        broll = self._broll_of(command, anchor, evidence.value)

        ctx.progress.report(2, _STEPS, "Fingerprinting the cuts")
        previous = self._previous(anchor, profile.name)
        decisions = _decisions(command, previous)
        config_fp = command.config.fingerprint(profile)
        facts = [self._skeleton(c, evidence.value, broll, config_fp, command) for c in cuts]
        key = self._cache_key(sequence, facts, evidence.value, decisions, config_fp)
        spec = DocumentSpec(RESULT_OPERATION, ANALYZER_VERSION, key)
        fingerprint = self._documents.fingerprint(anchor, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        cached = self._documents.load(anchor, fingerprint.value, analysis_from_json)
        if cached is not None:
            _log.info("Continuity analysis reused", asset_id=cached[1].id, cache_hit=True)
            return Ok(ContinuityResult(cached[0], cached[1], created=False))

        ctx.raise_if_cancelled()
        ctx.progress.report(3, _STEPS, "Reading the picture and sound at the cuts")
        known = {r.fingerprint for r in previous.transitions} if previous else set()
        transitions = [
            t if t.fingerprint in known else self._with_evidence(t, evidence.value, command)
            for t in facts
        ]

        ctx.progress.report(4, _STEPS, "Judging the cuts")
        anchor_asset = self._library.get(anchor)
        if isinstance(anchor_asset, Err):
            return anchor_asset
        version, supersedes = self._history(anchor, profile.name)
        digest_ = _digest(key)
        meta = AnalysisMeta(
            continuity_analysis_id=f"ca_{digest_[:16]}",
            analysis_version=version,
            supersedes=supersedes,
            project_id=sequence.project_id,
            created_at=self._clock.now(),
            edit_plan_id=sequence.edit_plan_id,
            edit_plan_version=sequence.edit_plan_version,
            configuration_fingerprint=digest_,
            story_format=sequence.story_format,
            sources=sequence.assets,
            inputs=tuple(evidence.value.records),
            stages=_stages(evidence.value, broll, command),
            warnings=(),
            decisions=decisions,
        )
        result = analyse_continuity(transitions, command.config, meta, previous)
        result = replace(result, metrics=replace(result.metrics, skipped=_skipped(sequence)))
        errors = [i.detail for i in result.issues_of(Severity.ERROR)]
        if errors:
            _log.error("Continuity analysis rejected its own result", problems=errors[:5])
            return Err(InvalidContinuityAnalysis(errors))

        ctx.progress.report(_STEPS, _STEPS, "Saving")
        stored = self._documents.store(
            anchor_asset.value,
            analysis_to_json(result),
            filename="continuity_analysis.json",
            spec=spec,
            display_name=f"{anchor_asset.value.display_name} (continuity)",
            metadata={
                "profile": profile.name,
                "edit_plan_id": sequence.edit_plan_id,
                "edit_plan_version": sequence.edit_plan_version,
                "analysis_version": result.analysis_version,
                "issues": len(result.issues),
                "state": result.state.value,
            },
        )
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Continuity analysis created",
            asset_id=stored.value.id,
            transitions=result.metrics.transitions,
            evaluated=result.metrics.evaluated,
            reused=result.metrics.reused,
            issues=len(result.issues),
        )
        return Ok(ContinuityResult(result, stored.value, created=True))

    # --- evidence ----------------------------------------------------------------------------
    def _evidence(
        self, command: AnalyzeContinuityCommand, sequence: ProposedSequence
    ) -> Result[_Evidence, AnalyzeContinuityError]:
        out = _Evidence()
        for asset_id in sequence.assets:
            found = self._library.get(asset_id)
            if isinstance(found, Err):
                return found
            video = (
                _read(command.video_analyses, asset_id, self._video.latest, "video_analysis", out)
                if command.use_video
                else None
            )
            audio = (
                _read(command.audio_timelines, asset_id, self._audio.latest, "audio_analysis", out)
                if command.use_audio
                else None
            )
            alignment = (
                _read(command.alignments, asset_id, self._alignments.latest, "alignment", out)
                if command.use_alignment
                else None
            )
            if video is not None:
                out.video[asset_id] = video
            if audio is not None:
                out.audio[asset_id] = audio
            if alignment is not None:
                out.alignment[asset_id] = alignment
            out.related[asset_id] = self._related(asset_id)
            out.identities[asset_id] = {
                "video": f"{video.asset_checksum}/{video.provenance.profile_name}"
                if video
                else None,
                "audio": f"{audio.duration}/{len(audio.words)}" if audio else None,
                "alignment": alignment.alignment_id if alignment else None,
                "related": list(out.related[asset_id]),
            }
        return Ok(out)

    def _related(self, asset_id: str) -> tuple[str, ...]:
        page = self._relationships.relationships_of(
            asset_id, RelationshipQuery(types=_RELATED, page_size=50)
        )
        if isinstance(page, Err):
            return ()
        return tuple(
            sorted(
                {
                    r.target_asset_id if r.source_asset_id == asset_id else r.source_asset_id
                    for r in page.value.items
                    if r.status is RelationshipStatus.CONFIRMED or r.status is None
                }
            )
        )

    def _broll_of(
        self, command: AnalyzeContinuityCommand, anchor: str, out: _Evidence
    ) -> BrollAnalysis | None:
        found: BrollAnalysis | None = None
        how, detail = InputHow.NOT_AVAILABLE, "no B-roll analysis (cutaway availability unknown)"
        if command.broll is not None:
            found, how, detail = command.broll, InputHow.SUPPLIED, ""
        elif command.use_broll:
            stored = self._broll.latest(anchor)
            if isinstance(stored, Ok) and stored.value is not None:
                found, how, detail = stored.value[0], InputHow.REUSED, stored.value[1].id
        out.records.append(
            InputRecord(
                kind="broll_analysis",
                how=how,
                identity=None if found is None else found.broll_analysis_id,
                detail=detail,
            )
        )
        out.identities["broll"] = None if found is None else found.broll_analysis_id
        return found

    # --- the cuts ----------------------------------------------------------------------------
    def _skeleton(
        self,
        cut: "_Cut",
        evidence: _Evidence,
        broll: BrollAnalysis | None,
        config_fp: Mapping[str, object],
        command: AnalyzeContinuityCommand,
    ) -> TransitionFact:
        a, b = cut.before, cut.after
        related = tuple(
            sorted(
                (
                    set(evidence.related.get(a.asset_id, ()))
                    | set(evidence.related.get(b.asset_id, ()))
                )
                - {a.asset_id, b.asset_id}
            )
        )
        reordered = a.asset_id == b.asset_id and b.source_start < a.source_end - _EPS
        gap = (
            b.source_start - a.source_end
            if a.asset_id == b.asset_id and b.source_start > a.source_end + _EPS
            else None
        )
        kind = (
            TransitionKind.SAME_RECORDING
            if a.asset_id == b.asset_id
            else TransitionKind.CROSS_RECORDING
        )
        cover = _cover_available(broll, a, b)
        flags = (
            cut.covered,
            cut.bridged,
            a.speech_trim_out or b.speech_trim_in,
            a.section_id != b.section_id,
            reordered,
            bool(a.reuse_of or b.reuse_of),
        )
        fingerprint = digest(
            a.asset_id, a.source_start, a.source_end, b.asset_id, b.source_start, b.source_end,
            flags, related, cover, kind.value,
            json.dumps(
                [evidence.identities.get(a.asset_id), evidence.identities.get(b.asset_id)],
                sort_keys=True, default=str,
            ),
            json.dumps(config_fp, sort_keys=True, default=str),
            ANALYZER_VERSION, SCHEMA_VERSION, VOCABULARY_VERSION,
            command.use_video, command.use_audio, command.use_alignment,
        )  # fmt: skip
        return TransitionFact(
            transition_id=f"tr:{a.clip_id}>{b.clip_id}",
            kind=kind,
            outgoing=_side(a, outgoing=True),
            incoming=_side(b, outgoing=False),
            timeline_at=b.timeline_start,
            covered=cut.covered,
            bridged=cut.bridged,
            speech_trimmed=a.speech_trim_out or b.speech_trim_in,
            section_change=a.section_id != b.section_id,
            reordered=reordered,
            gap=gap,
            cold_open=bool(a.reuse_of or b.reuse_of),
            fingerprint=fingerprint,
            related_assets=related,
            cover_available=cover,
        )

    def _with_evidence(
        self, t: TransitionFact, evidence: _Evidence, command: AnalyzeContinuityCommand
    ) -> TransitionFact:
        out, inc = (
            self._read_side(t.outgoing, True, evidence),
            self._read_side(t.incoming, False, evidence),
        )
        kind = t.kind
        if (
            kind is TransitionKind.SAME_RECORDING
            and out.shot_id is not None
            and out.shot_id == inc.shot_id
        ):
            kind = TransitionKind.SAME_SHOT
        return replace(t, outgoing=out, incoming=inc, kind=kind)

    @staticmethod
    def _read_side(side: SideFact, outgoing: bool, evidence: _Evidence) -> SideFact:
        asset = side.asset_id
        video = evidence.video.get(asset)
        audio = evidence.audio.get(asset)
        alignment = evidence.alignment.get(asset)
        correction = alignment.video_correction if alignment is not None else 0.0
        edge = shot = scene = None
        if video is not None:
            found = edge_fact(video, side.moment, outgoing, correction)
            if found is not None:
                edge, shot, scene = found
        return replace(
            side,
            edge=edge,
            shot_id=shot,
            scene_id=scene,
            audio=audio_edge(audio, side.moment, outgoing) if audio is not None else None,
            sync=sync_state(alignment),
        )

    # --- identity, history -------------------------------------------------------------------
    @staticmethod
    def _cache_key(
        sequence: ProposedSequence,
        facts: Sequence[TransitionFact],
        evidence: _Evidence,
        decisions: tuple[IssueDecision, ...],
        config_fp: Mapping[str, object],
    ) -> Mapping[str, JsonValue]:
        return {
            "plan": [sequence.edit_plan_id, sequence.edit_plan_version],
            "cuts": [t.fingerprint for t in facts],
            "inputs": dict(evidence.identities),
            "decisions": [[d.issue_id, d.decision.value, d.note] for d in decisions],
            "config": json.loads(json.dumps(config_fp)),
            "project": sequence.project_id,
            "versions": {
                "analyzer": ANALYZER_VERSION,
                "schema": SCHEMA_VERSION,
                "vocabulary": VOCABULARY_VERSION,
            },
        }

    def _previous(self, anchor: str, profile: str) -> ContinuityAnalysis | None:
        found = self._results.latest(anchor, profile=profile)
        return found.value[0] if isinstance(found, Ok) and found.value is not None else None

    def _history(self, anchor: str, profile: str) -> tuple[int, str | None]:
        """1 for the first analysis of this recording and profile; later ones count up and name
        their predecessor. Earlier analyses are never changed or removed."""
        earlier = [
            a
            for a in self._library.list_derived(anchor)
            if a.metadata.get("processing_type") == RESULT_OPERATION
            and a.metadata.get("profile") == profile
        ]
        if not earlier:
            return 1, None
        newest = max(earlier, key=lambda a: (a.created_at, a.id))
        return len(earlier) + 1, newest.id


# --- helpers ---------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Cut:
    before: ProposedClip
    after: ProposedClip
    covered: bool
    bridged: bool


def _problems(sequence: ProposedSequence) -> list[str]:
    out: list[str] = []
    if not sequence.clips:
        out.append("the sequence has no clips")
    ids = [c.clip_id for c in sequence.clips]
    if len(set(ids)) != len(ids):
        out.append("clip ids are not unique")
    for c in sequence.clips:
        if c.source_end <= c.source_start or c.source_start < 0:
            out.append(f"{c.clip_id}: the source range is empty or negative")
        if c.timeline_end <= c.timeline_start:
            out.append(f"{c.clip_id}: the timeline place is empty")
    return out


def _main(sequence: ProposedSequence) -> list[ProposedClip]:
    return sorted((c for c in sequence.clips if c.layer == 0), key=lambda c: c.timeline_start)


def _cuts(sequence: ProposedSequence) -> list[_Cut]:
    covers = [c for c in sequence.clips if c.layer > 0]
    out = []
    for before, after in pairwise(_main(sequence)):
        continuous = (
            before.asset_id == after.asset_id
            and abs(after.source_start - before.source_end) <= _EPS
        )
        if continuous:
            continue  # plain join of contiguous material: nothing was cut
        at = after.timeline_start
        out.append(
            _Cut(
                before,
                after,
                covered=any(c.timeline_start <= at < c.timeline_end for c in covers),
                bridged=before.audio_bridge_out or after.audio_bridge_in,
            )
        )
    return out


def _skipped(sequence: ProposedSequence) -> int:
    return max(0, len(_main(sequence)) - 1 - len(_cuts(sequence)))


def _side(clip: ProposedClip, *, outgoing: bool) -> SideFact:
    return SideFact(
        clip_id=clip.clip_id,
        asset_id=clip.asset_id,
        section_id=clip.section_id,
        beat_id=clip.beat_id,
        clip_start=clip.source_start,
        clip_end=clip.source_end,
        moment=clip.source_end if outgoing else clip.source_start,
    )


def _cover_available(broll: BrollAnalysis | None, a: ProposedClip, b: ProposedClip) -> bool | None:
    """Whether the B-roll analysis has a cutaway for the place of the cut (same recording only)."""
    if broll is None or a.asset_id != b.asset_id:
        return None
    at = float(a.source_end)
    for rec in broll.recommendations:
        if rec.asset_id != a.asset_id:
            continue
        start, end = rec.placement.target_start.value, rec.placement.target_end.value
        if start - 0.5 <= at <= end + 0.5:
            return not rec.missing_coverage
    return None


def _decisions(
    command: AnalyzeContinuityCommand, previous: ContinuityAnalysis | None
) -> tuple[IssueDecision, ...]:
    chosen = {d.issue_id: d for d in command.decisions}
    if command.carry_decisions and previous is not None:
        for d in previous.decisions:
            chosen.setdefault(d.issue_id, d)
    return tuple(sorted(chosen.values(), key=lambda d: d.issue_id))


def _stages(
    evidence: _Evidence, broll: BrollAnalysis | None, command: AnalyzeContinuityCommand
) -> tuple[StageReport, ...]:
    def stage(name: str, present: bool, reason: str) -> StageReport:
        return StageReport(
            stage=name,
            state=StageState.OK if present else StageState.NOT_AVAILABLE,
            reason="" if present else reason,
        )

    return (
        stage(
            "picture", bool(evidence.video), "no video analysis: picture continuity was not judged"
        ),
        stage("sound", bool(evidence.audio), "no audio analysis: sound continuity was not judged"),
        stage("alignment", bool(evidence.alignment), "no alignment: sync was not verified"),
        stage("broll", broll is not None, "no B-roll analysis: cutaway availability unknown"),
        StageReport(
            stage="alternatives",
            state=StageState.SKIPPED,
            reason="alternate takes are named, not compared",
        ),
    )


def _read[T](
    supplied: Mapping[str, T],
    asset_id: str,
    latest: Callable[[str], Result[tuple[T, MediaAssetDto] | None, MediaHouseError]],
    kind: str,
    out: _Evidence,
) -> T | None:
    """The result the caller supplied, else the stored one, else ``None`` (recorded as such)."""
    if asset_id in supplied:
        out.records.append(InputRecord(kind=kind, how=InputHow.SUPPLIED, asset_id=asset_id))
        return supplied[asset_id]
    stored = latest(asset_id)
    if isinstance(stored, Ok) and stored.value is not None:
        out.records.append(
            InputRecord(
                kind=kind, how=InputHow.REUSED, asset_id=asset_id, detail=stored.value[1].id
            )
        )
        return stored.value[0]
    detail = (
        f"no stored {kind.replace('_', ' ')}"
        if isinstance(stored, Ok)
        else f"the library could not be read: {stored.error.user_message}"
    )
    out.records.append(
        InputRecord(kind=kind, how=InputHow.NOT_AVAILABLE, asset_id=asset_id, detail=detail)
    )
    return None


def _digest(key: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()


__all__ = [
    "AnalyzeContinuity",
    "AnalyzeContinuityCommand",
    "AnalyzeContinuityError",
    "ContinuityResult",
]
