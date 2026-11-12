"""The production workflow: one application service the UI (and the pipeline CLI) talks to.

It only delegates: every rule lives in the step it names. Methods are blocking; callers run them
as jobs. ``ctx`` is the job context (progress, cancellation) for the long ones.
"""

from collections.abc import Sequence
from pathlib import Path

from media_house.modules.production_project.application.brief_step import (
    BriefDocuments,
    BuildBriefDocuments,
    DecideRisk,
    ExtractBrief,
    ExtractReport,
    ReadBrief,
    RunRiskPanel,
)
from media_house.modules.production_project.application.context_step import (
    BuildContext,
    ChooseContext,
    EditContext,
    RecordEvaluation,
    RunVariant,
)
from media_house.modules.production_project.application.final_step import (
    AttachTimeline,
    BuildMetadata,
    CheckGate,
    ExportPackage,
    ExportReport,
    GateReport,
)
from media_house.modules.production_project.application.material_step import (
    BuildRecordingGuide,
    IngestFiles,
    IngestReport,
    PlanMaterial,
    SettleFlag,
)
from media_house.modules.production_project.application.ports import AuditEntry, ProjectSummary
from media_house.modules.production_project.application.projects import ProjectCatalog
from media_house.modules.production_project.application.story_step import (
    Brainstorm,
    ChooseConcept,
    EditFacts,
    EditScript,
    GenerateScript,
    LintScript,
    RewriteProposal,
)
from media_house.modules.production_project.application.views import ScriptReport
from media_house.modules.production_project.domain.claims import RiskAction
from media_house.modules.production_project.domain.context import RunMeasurements, Score
from media_house.modules.production_project.domain.documents import Document
from media_house.modules.production_project.domain.project import Project, ProjectSettings
from media_house.modules.production_project.domain.values import FlagKind, Resolution
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import (
    ConflictError,
    Err,
    NotFoundError,
    Result,
    ValidationError,
)

type Failure = NotFoundError | ConflictError | ValidationError


class ProductionWorkflowService:
    """Implements ``ProductionWorkflow`` (see ``contracts``)."""

    def __init__(
        self,
        projects: ProjectCatalog,
        read: ReadBrief,
        extract: ExtractBrief,
        risks: RunRiskPanel,
        decide: DecideRisk,
        briefs: BuildBriefDocuments,
        brainstorm: Brainstorm,
        choose: ChooseConcept,
        script: GenerateScript,
        edit: EditScript,
        facts: EditFacts,
        lint: LintScript,
        plan: PlanMaterial,
        ingest: IngestFiles,
        settle: SettleFlag,
        guide: BuildRecordingGuide,
        context: BuildContext,
        edit_context: EditContext,
        evaluate: RecordEvaluation,
        choose_context: ChooseContext,
        metadata: BuildMetadata,
        gate: CheckGate,
        export: ExportPackage,
        timeline: AttachTimeline | None = None,
        run_variant: RunVariant | None = None,
    ) -> None:
        self._projects = projects
        self._read = read
        self._extract = extract
        self._risks = risks
        self._decide = decide
        self._briefs = briefs
        self._brainstorm = brainstorm
        self._choose = choose
        self._script = script
        self._edit = edit
        self._facts = facts
        self._lint = lint
        self._plan = plan
        self._ingest = ingest
        self._settle = settle
        self._guide = guide
        self._context = context
        self._edit_context = edit_context
        self._evaluate = evaluate
        self._choose_context = choose_context
        self._metadata = metadata
        self._gate = gate
        self._export = export
        self._timeline = timeline
        self._run_variant = run_variant

    # --- projects -------------------------------------------------------------------------------
    def create_project(self, settings: ProjectSettings) -> Result[Project, ValidationError]:
        return self._projects.create(settings)

    def list_projects(self) -> Sequence[ProjectSummary]:
        return self._projects.list()

    def open_project(self, project_id: str) -> Result[Project, NotFoundError]:
        return self._projects.get(project_id)

    def audit(self, project_id: str) -> Sequence[AuditEntry]:
        return self._projects.audit(project_id)

    # --- 1 and 2: brief and risks ---------------------------------------------------------------
    def read_brief_file(self, path: Path) -> Result[str, ValidationError]:
        return self._read.execute(path)

    def extract_brief(
        self,
        project_id: str,
        text: str,
        *,
        ideas: str = "",
        links: tuple[str, ...] = (),
        use_model: bool = False,
        cancellation: CancellationToken | None = None,
    ) -> Result[ExtractReport, Failure]:
        return self._extract.execute(
            project_id,
            text,
            ideas=ideas,
            links=links,
            use_model=use_model,
            cancellation=cancellation,
        )

    def risk_panel(self, project_id: str) -> Result[Project, NotFoundError | ConflictError]:
        return self._risks.execute(project_id)

    def decide_risk(
        self,
        project_id: str,
        risk_id: str,
        action: RiskAction,
        *,
        reason: str = "",
        replacement: str = "",
        remember_for_client: bool = False,
    ) -> Result[Project, Failure]:
        return self._decide.execute(
            project_id,
            risk_id,
            action,
            reason=reason,
            replacement=replacement,
            remember_for_client=remember_for_client,
        )

    def brief_documents(
        self, project_id: str
    ) -> Result[BriefDocuments, NotFoundError | ValidationError]:
        return self._briefs.execute(project_id)

    def export_brief_documents(
        self, project_id: str, target: Path, formats: tuple[str, ...] = ("docx", "pdf")
    ) -> Result[tuple[Path, ...], NotFoundError | ValidationError]:
        return self._briefs.export(project_id, target, formats)

    # --- 3: story and script --------------------------------------------------------------------
    def brainstorm(
        self,
        project_id: str,
        *,
        rituals: Sequence[tuple[str, str]] = (),
        use_learned: bool = True,
        cancellation: CancellationToken | None = None,
    ) -> Result[Project, Failure]:
        return self._brainstorm.execute(
            project_id, rituals=rituals, use_learned=use_learned, cancellation=cancellation
        )

    def choose_concept(
        self,
        project_id: str,
        concept_id: str,
        *,
        why: str = "",
        mix_parts: Sequence[tuple[str, str]] = (),
    ) -> Result[Project, Failure]:
        return self._choose.execute(project_id, concept_id, why=why, mix_parts=mix_parts)

    def generate_script(
        self,
        project_id: str,
        *,
        use_learned: bool = True,
        use_model: bool = True,
        cancellation: CancellationToken | None = None,
    ) -> Result[Project, Failure]:
        return self._script.execute(
            project_id, use_learned=use_learned, use_model=use_model, cancellation=cancellation
        )

    def edit_script(self, project_id: str, text: str) -> Result[Project, Failure]:
        return self._edit.set_text(project_id, text)

    def restore_script(self, project_id: str, version: int) -> Result[Project, Failure]:
        return self._edit.restore(project_id, version)

    def rewrite_scene(
        self, project_id: str, scene_id: str, instruction: str, cancellation: CancellationToken
    ) -> Result[Project, Failure]:
        return self._edit.rewrite_scene(project_id, scene_id, instruction, cancellation)

    def propose_rewrite(
        self, project_id: str, scene_id: str, instruction: str, cancellation: CancellationToken
    ) -> Result[RewriteProposal, Failure]:
        return self._edit.propose(project_id, scene_id, instruction, cancellation)

    def apply_proposal(
        self, project_id: str, proposal: RewriteProposal
    ) -> Result[Project, Failure]:
        return self._edit.apply(project_id, proposal)

    def fill_slot(self, project_id: str, key: str, value: str) -> Result[Project, Failure]:
        return self._facts.fill_slot(project_id, key, value)

    def set_fact(self, project_id: str, key: str, value: str) -> Result[Project, Failure]:
        return self._facts.set_fact(project_id, key, value)

    def verify_fact(
        self, project_id: str, key: str, source_ref: str, quote: str
    ) -> Result[Project, Failure]:
        return self._facts.verify_fact(project_id, key, source_ref, quote)

    def approve(self, project_id: str, gate: str) -> Result[Project, Failure]:
        return self._facts.approve(project_id, gate)

    def set_block(self, project_id: str, block: str, on: bool) -> Result[Project, Failure]:
        return self._facts.set_block(project_id, block, on)

    def override(
        self, project_id: str, finding_id: str, reason: str, *, remember_for_client: bool = False
    ) -> Result[Project, Failure]:
        return self._facts.override(
            project_id, finding_id, reason, remember_for_client=remember_for_client
        )

    def lint(self, project_id: str) -> Result[ScriptReport, NotFoundError]:
        return self._lint.execute(project_id)

    # --- 4: material ----------------------------------------------------------------------------
    def plan_material(self, project_id: str) -> Result[Project, Failure]:
        return self._plan.execute(project_id)

    def ingest(self, project_id: str, root: Path, ctx: JobContext) -> Result[IngestReport, Failure]:
        return self._ingest.execute(project_id, root, ctx)

    def settle_flag(
        self, project_id: str, file: str, kind: FlagKind, how: Resolution, note: str
    ) -> Result[Project, Failure]:
        return self._settle.execute(project_id, file, kind, how, note)

    def recording_guide(self, project_id: str) -> Result[Document, NotFoundError | ValidationError]:
        return self._guide.execute(project_id)

    # --- 5: context lab and finalisation --------------------------------------------------------
    def build_context(self, project_id: str) -> Result[Project, Failure]:
        return self._context.execute(project_id)

    def edit_context(self, project_id: str, level: str, text: str) -> Result[Project, Failure]:
        return self._edit_context.execute(project_id, level, text)

    def record_evaluation(
        self,
        project_id: str,
        level: str,
        scope: str,
        measurements: RunMeasurements,
        manual: Sequence[Score] = (),
        notes: str = "",
    ) -> Result[Project, Failure]:
        return self._evaluate.execute(project_id, level, scope, measurements, manual, notes)

    def choose_context(self, project_id: str, *, pick: str = "") -> Result[Project, Failure]:
        return self._choose_context.execute(project_id, pick=pick)

    def build_metadata(
        self, project_id: str, *, tracking_link: str = ""
    ) -> Result[Project, Failure]:
        return self._metadata.execute(project_id, tracking_link=tracking_link)

    def run_variant(
        self,
        project_id: str,
        level: str,
        paths: Sequence[Path],
        ctx: JobContext,
        *,
        scope: str = "excerpt",
        output_format: str = "16:9",
    ) -> Result[Project, Failure]:
        if self._run_variant is None:
            return Err(
                ValidationError(
                    "no pipeline",
                    user_message="Der Testlauf der Pipeline ist hier nicht verfügbar.",
                )
            )
        return self._run_variant.execute(
            project_id, level, paths, ctx, scope=scope, output_format=output_format
        )

    def attach_timeline(
        self, project_id: str, anchor_asset_id: str = ""
    ) -> Result[Project, Failure]:
        if self._timeline is None:
            return Err(
                ValidationError(
                    "no timeline source",
                    user_message="Die gebaute Timeline ist hier nicht verfügbar.",
                )
            )
        return self._timeline.execute(project_id, anchor_asset_id)

    def check_gate(self, project_id: str) -> Result[GateReport, NotFoundError]:
        return self._gate.execute(project_id)

    def export_package(
        self, project_id: str, root: Path, ctx: JobContext
    ) -> Result[ExportReport, Failure]:
        return self._export.execute(project_id, root, ctx)
