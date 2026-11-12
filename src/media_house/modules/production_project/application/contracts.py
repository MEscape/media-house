"""Public surface of production_project.

``ProductionWorkflow`` is the one service a UI or the pipeline CLI uses; the types it returns are
re-exported here (a presentation layer may not import a domain). The older pure functions of the
scripted-transcript reader stay available for the pipeline.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from media_house.modules.production_project.application.brief_step import (
    BriefDocuments,
    ExtractReport,
)
from media_house.modules.production_project.application.context_step import pipeline_context
from media_house.modules.production_project.application.final_step import ExportReport, GateReport
from media_house.modules.production_project.application.material_step import IngestReport
from media_house.modules.production_project.application.ports import AuditEntry, ProjectSummary
from media_house.modules.production_project.application.story_step import RewriteProposal
from media_house.modules.production_project.application.views import (
    ScriptReport,
    spoken_script,
)
from media_house.modules.production_project.domain.assets import AssetFlag
from media_house.modules.production_project.domain.brief import (
    BriefModel,
    Constraint,
    Fact,
    OpenQuestion,
)
from media_house.modules.production_project.domain.claims import ClaimRisk, RiskAction, RiskDecision
from media_house.modules.production_project.domain.compliance import Finding
from media_house.modules.production_project.domain.concepts import StoryConcept
from media_house.modules.production_project.domain.conform import (
    Conformance,
    SegmentMatch,
    Take,
    TranscriptLine,
    conform,
    sentence_lines,
)
from media_house.modules.production_project.domain.context import (
    ContextVariant,
    Criterion,
    EvalRun,
    RunMeasurements,
    Score,
)
from media_house.modules.production_project.domain.documents import Block, BlockKind, Document
from media_house.modules.production_project.domain.manifest import Manifest, ManifestEntry
from media_house.modules.production_project.domain.material import AssetRequirement
from media_house.modules.production_project.domain.metadata import Chapter, YoutubeMetadata
from media_house.modules.production_project.domain.project import Project, ProjectSettings
from media_house.modules.production_project.domain.reconcile import (
    CoverageState,
    Reconciliation,
    SceneCoverage,
)
from media_house.modules.production_project.domain.script import (
    Exclusion,
    Scene,
    Script,
    ScriptSentence,
    fact_rows,
    parse_script,
)
from media_house.modules.production_project.domain.script_tools import SceneLength
from media_house.modules.production_project.domain.slots import Slot
from media_house.modules.production_project.domain.values import (
    Artifact,
    ConstraintKind,
    FlagKind,
    Priority,
    Resolution,
    Role,
    Severity,
    SlotStatus,
    Timing,
)
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import ConflictError, NotFoundError, Result, ValidationError

type Failure = NotFoundError | ConflictError | ValidationError


class ProductionWorkflow(Protocol):
    """Brief to cut: every step of the wizard as one blocking call (run it as a job)."""

    def create_project(self, settings: ProjectSettings) -> Result[Project, ValidationError]: ...
    def list_projects(self) -> Sequence[ProjectSummary]: ...
    def open_project(self, project_id: str) -> Result[Project, NotFoundError]: ...
    def audit(self, project_id: str) -> Sequence[AuditEntry]: ...

    def read_brief_file(self, path: Path) -> Result[str, ValidationError]: ...
    def extract_brief(
        self,
        project_id: str,
        text: str,
        *,
        ideas: str = "",
        links: tuple[str, ...] = (),
        use_model: bool = False,
        cancellation: CancellationToken | None = None,
    ) -> Result[ExtractReport, Failure]: ...
    def risk_panel(self, project_id: str) -> Result[Project, NotFoundError | ConflictError]: ...
    def decide_risk(
        self,
        project_id: str,
        risk_id: str,
        action: RiskAction,
        *,
        reason: str = "",
        replacement: str = "",
        remember_for_client: bool = False,
    ) -> Result[Project, Failure]: ...
    def brief_documents(
        self, project_id: str
    ) -> Result[BriefDocuments, NotFoundError | ValidationError]: ...
    def export_brief_documents(
        self, project_id: str, target: Path, formats: tuple[str, ...] = ("docx", "pdf")
    ) -> Result[tuple[Path, ...], NotFoundError | ValidationError]: ...

    def brainstorm(
        self,
        project_id: str,
        *,
        rituals: Sequence[tuple[str, str]] = (),
        use_learned: bool = True,
        cancellation: CancellationToken | None = None,
    ) -> Result[Project, Failure]: ...
    def choose_concept(
        self,
        project_id: str,
        concept_id: str,
        *,
        why: str = "",
        mix_parts: Sequence[tuple[str, str]] = (),
    ) -> Result[Project, Failure]: ...
    def generate_script(
        self,
        project_id: str,
        *,
        use_learned: bool = True,
        use_model: bool = True,
        cancellation: CancellationToken | None = None,
    ) -> Result[Project, Failure]: ...
    def edit_script(self, project_id: str, text: str) -> Result[Project, Failure]: ...
    def restore_script(self, project_id: str, version: int) -> Result[Project, Failure]: ...
    def rewrite_scene(
        self, project_id: str, scene_id: str, instruction: str, cancellation: CancellationToken
    ) -> Result[Project, Failure]: ...
    def propose_rewrite(
        self, project_id: str, scene_id: str, instruction: str, cancellation: CancellationToken
    ) -> Result[RewriteProposal, Failure]: ...
    def apply_proposal(
        self, project_id: str, proposal: RewriteProposal
    ) -> Result[Project, Failure]: ...
    def fill_slot(self, project_id: str, key: str, value: str) -> Result[Project, Failure]: ...
    def set_fact(self, project_id: str, key: str, value: str) -> Result[Project, Failure]: ...
    def verify_fact(
        self, project_id: str, key: str, source_ref: str, quote: str
    ) -> Result[Project, Failure]: ...
    def approve(self, project_id: str, gate: str) -> Result[Project, Failure]: ...
    def set_block(self, project_id: str, block: str, on: bool) -> Result[Project, Failure]: ...
    def override(
        self, project_id: str, finding_id: str, reason: str, *, remember_for_client: bool = False
    ) -> Result[Project, Failure]: ...
    def lint(self, project_id: str) -> Result[ScriptReport, NotFoundError]: ...

    def plan_material(self, project_id: str) -> Result[Project, Failure]: ...
    def ingest(
        self, project_id: str, root: Path, ctx: JobContext
    ) -> Result[IngestReport, Failure]: ...
    def settle_flag(
        self, project_id: str, file: str, kind: FlagKind, how: Resolution, note: str
    ) -> Result[Project, Failure]: ...
    def recording_guide(
        self, project_id: str
    ) -> Result[Document, NotFoundError | ValidationError]: ...

    def build_context(self, project_id: str) -> Result[Project, Failure]: ...
    def edit_context(self, project_id: str, level: str, text: str) -> Result[Project, Failure]: ...
    def record_evaluation(
        self,
        project_id: str,
        level: str,
        scope: str,
        measurements: RunMeasurements,
        manual: Sequence[Score] = (),
        notes: str = "",
    ) -> Result[Project, Failure]: ...
    def choose_context(self, project_id: str, *, pick: str = "") -> Result[Project, Failure]: ...
    def build_metadata(
        self, project_id: str, *, tracking_link: str = ""
    ) -> Result[Project, Failure]: ...
    def run_variant(
        self,
        project_id: str,
        level: str,
        paths: Sequence[Path],
        ctx: JobContext,
        *,
        scope: str = "excerpt",
        output_format: str = "16:9",
    ) -> Result[Project, Failure]: ...
    def attach_timeline(
        self, project_id: str, anchor_asset_id: str = ""
    ) -> Result[Project, Failure]: ...
    def check_gate(self, project_id: str) -> Result[GateReport, NotFoundError]: ...
    def export_package(
        self, project_id: str, root: Path, ctx: JobContext
    ) -> Result[ExportReport, Failure]: ...


__all__ = [
    "Artifact",
    "AssetFlag",
    "AssetRequirement",
    "AuditEntry",
    "Block",
    "BlockKind",
    "BriefDocuments",
    "BriefModel",
    "CancellationToken",
    "Chapter",
    "ClaimRisk",
    "Conformance",
    "Constraint",
    "ConstraintKind",
    "ContextVariant",
    "CoverageState",
    "Criterion",
    "Document",
    "EvalRun",
    "Exclusion",
    "ExportReport",
    "ExtractReport",
    "Fact",
    "Finding",
    "FlagKind",
    "GateReport",
    "IngestReport",
    "Manifest",
    "ManifestEntry",
    "OpenQuestion",
    "Priority",
    "ProductionWorkflow",
    "Project",
    "ProjectSettings",
    "ProjectSummary",
    "Reconciliation",
    "Resolution",
    "RewriteProposal",
    "RiskAction",
    "RiskDecision",
    "Role",
    "RunMeasurements",
    "Scene",
    "SceneCoverage",
    "SceneLength",
    "Score",
    "Script",
    "ScriptReport",
    "ScriptSentence",
    "SegmentMatch",
    "Severity",
    "Slot",
    "SlotStatus",
    "StoryConcept",
    "Take",
    "Timing",
    "TranscriptLine",
    "YoutubeMetadata",
    "conform",
    "fact_rows",
    "parse_script",
    "pipeline_context",
    "sentence_lines",
    "spoken_script",
]
