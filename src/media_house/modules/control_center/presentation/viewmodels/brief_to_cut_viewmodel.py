"""Brief to Cut: the wizard's state. Every step is a background job on the production workflow;
an expected failure (``Err``) becomes a message, never a dialog, and nothing here has business
rules: it passes what the person typed to the workflow and shows what comes back."""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from media_house.modules.control_center.presentation import strings
from media_house.modules.production_project.application.contracts import (
    FlagKind,
    ProductionWorkflow,
    ProjectSettings,
    Resolution,
    RewriteProposal,
    RiskAction,
)
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok

type Work = Callable[[JobContext], Any]


class BriefToCutViewModel(QObject):
    projects_changed = Signal(object)  # Sequence[ProjectSummary]
    project_changed = Signal(object)  # Project
    report_changed = Signal(object)  # ScriptReport
    ingest_changed = Signal(object)  # IngestReport
    gate_changed = Signal(object)  # GateReport
    proposal_changed = Signal(object)  # RewriteProposal | None
    brief_text_loaded = Signal(str)
    message_changed = Signal(str)  # empty clears
    busy_changed = Signal(bool)
    operation_changed = Signal(str)

    def __init__(
        self,
        workflow: ProductionWorkflow,
        jobs: UiJobRunner,
        status: StatusReporter,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._workflow = workflow
        self._jobs = jobs
        self._status = status
        self._project_id = ""
        self._pending = 0
        self._report_serial = 0

    @property
    def project_id(self) -> str:
        return self._project_id

    # --- plumbing -------------------------------------------------------------------------------
    def _run(self, name: str, work: Work, on_ok: Callable[[Any], None] | None = None) -> None:
        if self._pending:
            self.message_changed.emit(strings.B2C_ALREADY_BUSY)
            return
        self._pending += 1
        self.busy_changed.emit(True)
        self.operation_changed.emit(strings.B2C_OPERATION.format(name=name))
        self.message_changed.emit("")

        def done(result: Any) -> None:
            match result:
                case Err(error):
                    self.message_changed.emit(error.user_message)
                case Ok(value) if on_ok is not None:
                    on_ok(value)
                case _:
                    pass

        self._jobs.submit(name, work, on_success=done, on_finished=self._finished)

    def _finished(self) -> None:
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self.busy_changed.emit(False)
            self.operation_changed.emit("")

    def _updated(self, project: Any) -> None:
        self._project_id = project.project_id
        self.project_changed.emit(project)

    def _on_project(self, work: Callable[[JobContext, str], Any], name: str) -> None:
        if not self._project_id:
            self.message_changed.emit(strings.B2C_NO_PROJECT)
            return
        pid = self._project_id
        self._run(name, lambda ctx: work(ctx, pid), self._updated)

    # --- projects -------------------------------------------------------------------------------
    def refresh_projects(self) -> None:
        self._jobs.submit(
            "Projekte laden",
            lambda _ctx: self._workflow.list_projects(),
            on_success=lambda rows: self.projects_changed.emit(rows),
        )

    def create_project(
        self, title: str, client: str, format_name: str, year: int, *, sponsored: bool = True
    ) -> None:
        settings = ProjectSettings("", title, client, format_name, year, sponsored=sponsored)
        self._run(
            "Projekt anlegen", lambda _c: self._workflow.create_project(settings), self._opened
        )

    def _opened(self, project: Any) -> None:
        self._updated(project)
        self.refresh_projects()

    def open_project(self, project_id: str) -> None:
        self._run(
            "Projekt öffnen", lambda _c: self._workflow.open_project(project_id), self._updated
        )

    # --- 1 and 2 --------------------------------------------------------------------------------
    def load_brief_file(self, path: Path) -> None:
        self._run(
            "Briefing lesen",
            lambda _c: self._workflow.read_brief_file(path),
            lambda text: self.brief_text_loaded.emit(text),
        )

    def extract(self, text: str, ideas: str, *, use_model: bool = False) -> None:
        if not self._project_id:
            self.message_changed.emit(strings.B2C_NO_PROJECT)
            return
        pid = self._project_id
        self._run(
            "Briefing einlesen",
            lambda ctx: self._workflow.extract_brief(
                pid, text, ideas=ideas, use_model=use_model, cancellation=ctx.cancellation
            ),
            self._extracted,
        )

    def _extracted(self, report: Any) -> None:
        self._updated(report.project)
        if report.model_note:
            self.message_changed.emit(strings.B2C_MODEL_NOTE.format(note=report.model_note))

    def run_risks(self) -> None:
        self._on_project(lambda _c, pid: self._workflow.risk_panel(pid), "Risiken prüfen")

    def decide_risk(self, risk_id: str, keep: bool, reason: str = "", client: bool = False) -> None:
        action = RiskAction.KEPT if keep else RiskAction.REPLACED
        self._on_project(
            lambda _c, pid: self._workflow.decide_risk(
                pid, risk_id, action, reason=reason, remember_for_client=client
            ),
            "Entscheidung speichern",
        )

    def export_brief_documents(self, target: Path) -> None:
        if not self._project_id:
            return
        pid = self._project_id
        self._run(
            "Dokumente exportieren",
            lambda _c: self._workflow.export_brief_documents(pid, target),
            lambda paths: self._status.show(strings.B2C_DOCS_EXPORTED.format(count=len(paths))),
        )

    # --- 3 --------------------------------------------------------------------------------------
    def brainstorm(self, rituals: Sequence[tuple[str, str]] = ()) -> None:
        self._on_project(
            lambda ctx, pid: self._workflow.brainstorm(
                pid, rituals=rituals, cancellation=ctx.cancellation
            ),
            "Brainstorm",
        )

    def choose_concept(self, concept_id: str, why: str = "") -> None:
        self._on_project(
            lambda _c, pid: self._workflow.choose_concept(pid, concept_id, why=why),
            "Konzept wählen",
        )

    def generate_script(self) -> None:
        self._on_project(
            lambda ctx, pid: self._workflow.generate_script(pid, cancellation=ctx.cancellation),
            "Script erzeugen",
        )

    def save_script(self, text: str) -> None:
        self._on_project(lambda _c, pid: self._workflow.edit_script(pid, text), "Script speichern")

    def restore_script(self, version: int) -> None:
        self._on_project(
            lambda _c, pid: self._workflow.restore_script(pid, version), "Version wiederherstellen"
        )

    def fill_slot(self, key: str, value: str) -> None:
        self._on_project(
            lambda _c, pid: self._workflow.fill_slot(pid, key, value), "Feld speichern"
        )

    def refresh_report(self, text: str | None = None) -> None:
        if not self._project_id or self._pending:
            return
        pid = self._project_id
        self._report_serial += 1
        serial = self._report_serial

        def show(result: Any) -> None:
            if serial == self._report_serial and pid == self._project_id and isinstance(result, Ok):
                self.report_changed.emit(result.value)

        self._jobs.submit(
            "Script prüfen",
            lambda _c: self._workflow.lint(pid, text=text),
            on_success=show,
        )

    def propose_rewrite(self, scene_id: str, instruction: str) -> None:
        if not self._project_id:
            self.message_changed.emit(strings.B2C_NO_PROJECT)
            return
        pid = self._project_id
        self._run(
            "Vorschlag holen",
            lambda ctx: self._workflow.propose_rewrite(
                pid, scene_id, instruction, ctx.cancellation
            ),
            lambda proposal: self.proposal_changed.emit(proposal),
        )

    def apply_proposal(self, proposal: RewriteProposal) -> None:
        self.proposal_changed.emit(None)
        self._on_project(
            lambda _c, pid: self._workflow.apply_proposal(pid, proposal), "Änderung übernehmen"
        )

    def discard_proposal(self) -> None:
        self.proposal_changed.emit(None)

    # --- 4 --------------------------------------------------------------------------------------
    def plan_material(self) -> None:
        self._on_project(lambda _c, pid: self._workflow.plan_material(pid), "Materialplan")

    def ingest(self, root: Path) -> None:
        if not self._project_id:
            self.message_changed.emit(strings.B2C_NO_PROJECT)
            return
        pid = self._project_id
        self._run(
            "Material sortieren",
            lambda ctx: self._workflow.ingest(pid, root, ctx),
            self._ingested,
        )

    def _ingested(self, report: Any) -> None:
        self._updated(report.project)
        self.ingest_changed.emit(report)

    def settle(self, file: str, kind: FlagKind, how: Resolution, note: str) -> None:
        self._on_project(
            lambda _c, pid: self._workflow.settle_flag(pid, file, kind, how, note), "Markierung"
        )

    # --- 5 --------------------------------------------------------------------------------------
    def build_context(self) -> None:
        self._on_project(lambda _c, pid: self._workflow.build_context(pid), "Kontext erzeugen")

    def edit_context(self, level: str, text: str) -> None:
        self._on_project(
            lambda _c, pid: self._workflow.edit_context(pid, level, text), "Kontext speichern"
        )

    def run_variant(self, level: str, paths: Sequence[Path]) -> None:
        self._on_project(
            lambda ctx, pid: self._workflow.run_variant(pid, level, paths, ctx),
            "Testlauf der Pipeline",
        )

    def choose_context(self) -> None:
        self._on_project(lambda _c, pid: self._workflow.choose_context(pid), "Kontext wählen")

    def attach_timeline(self) -> None:
        self._on_project(lambda _c, pid: self._workflow.attach_timeline(pid), "Timeline einlesen")

    def build_metadata(self, tracking_link: str) -> None:
        self._on_project(
            lambda _c, pid: self._workflow.build_metadata(pid, tracking_link=tracking_link),
            "Metadaten",
        )

    def check_gate(self) -> None:
        if not self._project_id:
            return
        pid = self._project_id
        self._run(
            "Prüfen",
            lambda _c: self._workflow.check_gate(pid),
            lambda report: self.gate_changed.emit(report),
        )

    def export_package(self, root: Path) -> None:
        if not self._project_id:
            return
        pid = self._project_id
        self._run(
            "Paket exportieren",
            lambda ctx: self._workflow.export_package(pid, root, ctx),
            lambda report: self._status.show(strings.B2C_EXPORTED.format(path=report.root)),
        )
