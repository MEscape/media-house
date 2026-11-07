"""Gathering what the final review rests on: the session, the exact timeline, the reports of that
version, the mix, the Resolve binding and the output profile. Reading only; nothing is changed."""

from dataclasses import dataclass

from media_house.modules.audio_mix_engine.application.contracts import MixCatalog, MixResult
from media_house.modules.final_polish.application.contracts import (
    FinalPolishReport,
    PolishCatalog,
)
from media_house.modules.final_polish.application.contracts import (
    Severity as PolishSeverity,
)
from media_house.modules.quality_control.application.contracts import QualityCatalog, QualityReport
from media_house.modules.render_manager.application.contracts import (
    ProfileResolution,
    RenderProfiles,
)
from media_house.modules.resolve_adapter.application.contracts import ResolveCatalog, SyncState
from media_house.modules.resolve_export.domain.errors import NotReadyForExport
from media_house.modules.resolve_export.domain.readiness import (
    IssueFact,
    PolishProblemFact,
    ReadinessFacts,
)
from media_house.modules.review_orchestrator.application.contracts import (
    ReviewCatalog,
    ReviewSession,
)
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimeline,
    BuiltTimelineCatalog,
)
from media_house.shared.errors import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class ExportInputs:
    session: ReviewSession
    timeline: BuiltTimeline
    quality: QualityReport | None
    polish: FinalPolishReport | None
    mix: MixResult | None
    binding: SyncState | None
    resolution: ProfileResolution | None
    facts: ReadinessFacts

    @property
    def config_hash(self) -> str:
        return self.resolution.config_hash if self.resolution else ""


class InputsReader:
    def __init__(
        self,
        reviews: ReviewCatalog,
        builds: BuiltTimelineCatalog,
        qualities: QualityCatalog,
        polishes: PolishCatalog,
        mixes: MixCatalog,
        resolve: ResolveCatalog,
        profiles: RenderProfiles,
    ) -> None:
        self._reviews = reviews
        self._builds = builds
        self._qualities = qualities
        self._polishes = polishes
        self._mixes = mixes
        self._resolve = resolve
        self._profiles = profiles

    def read(
        self, session_id: str, profile_id: str, *, high_issues_block: bool | None = None
    ) -> Result[ExportInputs, NotReadyForExport]:
        found = self._reviews.get(session_id)
        if isinstance(found, Err):
            return Err(NotReadyForExport([str(found.error.user_message)]))
        session = found.value
        latest = self._builds.latest(
            session.anchor_asset_id, timeline_id=session.timeline.timeline_id
        )
        if isinstance(latest, Err) or latest.value is None:
            return Err(NotReadyForExport(["There is no built timeline for this review."]))
        timeline = latest.value[0]
        tl_hash = timeline.build.build_hash
        quality = self._quality(session, tl_hash)
        polish = self._polish(session, tl_hash)
        mixed = self._mixes.latest_result(session.anchor_asset_id, session.timeline.timeline_id)
        mix = mixed.value[0] if isinstance(mixed, Ok) and mixed.value is not None else None
        bound = self._resolve.latest_binding(session.anchor_asset_id, session.timeline.timeline_id)
        binding = bound.value[0] if isinstance(bound, Ok) and bound.value is not None else None
        resolved = self._profiles.resolve(
            profile_id,
            anchor_asset_id=session.anchor_asset_id,
            timeline_id=session.timeline.timeline_id,
        )
        resolution = resolved.value if isinstance(resolved, Ok) else None
        known = isinstance(resolved, Ok)
        problems = resolution.problems if resolution else ()
        facts = ReadinessFacts(
            timeline_hash=tl_hash,
            review_hash=session.timeline.content_hash,
            quality_report_id=quality.quality_report_id if quality else "",
            quality_hash=quality.timeline_content_hash if quality else "",
            quality_issues=tuple(
                IssueFact(i.issue_id, i.code, i.severity.value, i.description, i.blocks_export)
                for i in (quality.detected_issues if quality else ())
            ),
            polish_report_id=polish.final_polish_report_id if polish else "",
            polish_hash=polish.timeline_content_hash if polish else "",
            polish_readiness=polish.readiness_status.value if polish else "",
            polish_problems=tuple(
                PolishProblemFact(f.code, f.message, f.reference, f.start_frame)
                for f in (polish.remaining_issues if polish else ())
                if f.severity is PolishSeverity.PROBLEM
            ),
            mix_state=mix.state.value if mix else "",
            mix_errors=tuple(
                i.message
                for i in (mix.issues if mix else ())
                if i.severity.value in ("error", "critical") and i.certainty.value != "intentional"
            ),
            offline_sources=tuple(s.name for s in timeline.sources if not s.available),
            resolve_hash=binding.build_hash if binding else None,
            resolve_conflicts=tuple(c.element_id for c in (binding.conflicts if binding else ())),
            profile_known=known,
            profile_problems=problems,
            high_issues_block=session.policy.high_issues_block_export
            if high_issues_block is None
            else high_issues_block,
        )
        return Ok(ExportInputs(session, timeline, quality, polish, mix, binding, resolution, facts))

    def _quality(self, session: ReviewSession, tl_hash: str) -> QualityReport | None:
        got = self._qualities.latest(
            session.anchor_asset_id,
            timeline_id=session.timeline.timeline_id,
            timeline_content_hash=tl_hash,
        )
        return got.value[0] if isinstance(got, Ok) and got.value is not None else None

    def _polish(self, session: ReviewSession, tl_hash: str) -> FinalPolishReport | None:
        got = self._polishes.latest(
            session.anchor_asset_id,
            timeline_id=session.timeline.timeline_id,
            timeline_content_hash=tl_hash,
        )
        return got.value[0] if isinstance(got, Ok) and got.value is not None else None
