"""Evidence that can be looked at: the picture of the exact version, kept in the Media Library.

A finding about the picture is worth little without the picture. The frames are extracted from the
preview that was analysed and registered as derived assets of it; their identity includes the
timeline's content hash, so a frame can never be shown as evidence of another version.
"""

from dataclasses import replace
from fractions import Fraction
from pathlib import Path

from media_house.core.application.ports import MediaAnalyzer
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    MediaLibrary,
)
from media_house.modules.quality_control.domain.facts import TimelineFacts
from media_house.modules.quality_control.domain.model import QualityIssue
from media_house.modules.quality_control.domain.values import (
    EVIDENCE_OPERATION,
    SEVERITY_RANK,
    Category,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, ExternalSystemError, OperationCancelledError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)
MAX_EVIDENCE_FRAMES = 40
_CATEGORIES = (Category.VISUAL, Category.TECHNICAL_VIDEO)


class EvidenceRegistrar:
    def __init__(self, library: MediaLibrary, analyzer: MediaAnalyzer) -> None:
        self._library = library
        self._analyzer = analyzer

    def attach(
        self,
        issues: list[QualityIssue],
        timeline: TimelineFacts,
        *,
        preview_asset_id: str,
        preview_path: Path,
        sampled: dict[Fraction, Path],
        work_dir: Path,
        ctx: JobContext,
    ) -> list[QualityIssue]:
        """The issues, each with the extracted picture of its place when there is one."""
        wanted = [
            i
            for i in issues
            if i.category in _CATEGORIES
            and i.start_frame is not None
            and i.code != "render.decode_errors"
        ]
        wanted.sort(key=lambda i: (-SEVERITY_RANK[i.severity], i.start_frame or 0))
        evidence: dict[str, tuple[str, ...]] = {}
        for issue in wanted[:MAX_EVIDENCE_FRAMES]:
            ctx.raise_if_cancelled()
            frame = (issue.start_frame or 0) + (
                ((issue.end_frame or 0) - (issue.start_frame or 0)) // 2
                if issue.end_frame is not None
                else 0
            )
            moment = Fraction(frame) / timeline.settings.fps
            picture = self._picture(preview_path, moment, sampled, work_dir, ctx)
            if picture is None:
                continue
            asset_id = self._register(preview_asset_id, picture, timeline, issue, frame)
            if asset_id:
                evidence[issue.issue_id] = (asset_id,)
        return [
            replace(i, evidence_assets=evidence.get(i.issue_id, i.evidence_assets)) for i in issues
        ]

    def _picture(
        self,
        path: Path,
        moment: Fraction,
        sampled: dict[Fraction, Path],
        work_dir: Path,
        ctx: JobContext,
    ) -> Path | None:
        near = min(sampled, key=lambda t: abs(t - moment), default=None)
        if near is not None and abs(near - moment) <= Fraction(1, 25):
            return sampled[near]
        try:
            taken = self._analyzer.sample_frames(
                path, [moment], into=work_dir / "evidence", cancellation=ctx.cancellation
            )
        except OperationCancelledError:
            raise
        except ExternalSystemError as exc:
            _log.warning("Evidence frame skipped", reason=str(exc))
            return None
        return taken[0].path if taken and taken[0].path else None

    def _register(
        self,
        preview_asset_id: str,
        picture: Path,
        timeline: TimelineFacts,
        issue: QualityIssue,
        frame: int,
    ) -> str | None:
        registered = self._library.register_derived(
            preview_asset_id,
            picture,
            operation=EVIDENCE_OPERATION,
            config={
                "timeline_id": timeline.timeline_id,
                "timeline_content_hash": timeline.content_hash,
                "frame": frame,
                "issue": issue.fingerprint,
            },
            display_name=f"Evidence {issue.code} @ frame {frame}",
            metadata={
                "timeline_version": timeline.version,
                "issue_code": issue.code,
                "frame": frame,
            },
            role=AssetRole.PREVIEW,
        )
        if isinstance(registered, Err):
            _log.warning("Evidence frame not stored", reason=str(registered.error))
            return None
        return registered.value.asset.id
