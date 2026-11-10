"""Review workspace state and intents. Every read and change runs as a background job."""

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from media_house.modules.control_center.application.review_workspace import (
    ReadinessReport,
    ReviewSnapshot,
    ReviewWorkspace,
    Verdict,
)
from media_house.modules.control_center.presentation import strings
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.errors.handler import ErrorReport


class ReviewViewModel(QObject):
    anchors_changed = Signal(object)  # list[AnchorAsset]
    profiles_changed = Signal(object)  # list[ExportProfile]
    sessions_changed = Signal(object)  # list[ReviewSession]
    snapshot_changed = Signal(object)  # ReviewSnapshot | None
    busy_changed = Signal(bool)
    error_changed = Signal(str)  # empty clears
    can_act_changed = Signal(bool)  # a reviewer name and a session are set
    #: Readiness of the export the person is about to authorize: the view must confirm it.
    confirm_export_requested = Signal(object)  # ReadinessReport
    feedback_accepted = Signal()  # the view may clear its input

    def __init__(
        self,
        workspace: ReviewWorkspace,
        jobs: UiJobRunner,
        status: StatusReporter,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ws = workspace
        self._jobs = jobs
        self._status = status
        self._pending = 0
        self._anchor = ""
        self._reviewer = ""
        self._snapshot: ReviewSnapshot | None = None

    # --- state ----------------------------------------------------------------------------------
    @property
    def reviewer(self) -> str:
        return self._reviewer

    @property
    def snapshot(self) -> ReviewSnapshot | None:
        return self._snapshot

    @property
    def can_act(self) -> bool:
        return bool(self._reviewer) and self._snapshot is not None

    def set_reviewer(self, name: str) -> None:
        before = self.can_act
        self._reviewer = name.strip()
        if self.can_act != before:
            self.can_act_changed.emit(self.can_act)

    # --- reads ----------------------------------------------------------------------------------
    def load(self) -> None:
        self._submit("Load recordings", lambda: self._ws.anchors(), self.anchors_changed.emit)
        self._submit(
            "Load export profiles", lambda: self._ws.export_profiles(), self.profiles_changed.emit
        )

    def select_anchor(self, asset_id: str) -> None:
        self._anchor = asset_id
        self._set_snapshot(None)
        if not asset_id:
            self.sessions_changed.emit([])
            return
        self._load_sessions()

    def _load_sessions(self) -> None:
        anchor = self._anchor
        self._submit(
            "Load review sessions", lambda: self._ws.sessions(anchor), self.sessions_changed.emit
        )

    @property
    def session_id(self) -> str:
        return self._snapshot.session.session_id if self._snapshot else ""

    def select_session(self, session_id: str) -> None:
        if not session_id:
            self._set_snapshot(None)
            return
        self._submit("Load review", lambda: self._ws.snapshot(session_id), self._on_snapshot)

    def reload(self) -> None:
        if self._snapshot is not None:
            self.select_session(self._snapshot.session.session_id)

    # --- changes --------------------------------------------------------------------------------
    def add_feedback(self, text: str, section_id: str | None) -> None:
        self._change(
            "Add review feedback",
            lambda s: self._ws.add_feedback(
                s.session.session_id, text, author=self._reviewer, section_id=section_id
            ),
            strings.DONE_FEEDBACK,
            self.feedback_accepted.emit,
        )

    def lock_section(self, section_id: str, note: str) -> None:
        self._change(
            "Lock section",
            lambda s: self._ws.lock_section(
                s.session.session_id,
                section_id,
                timeline=s.session.timeline,
                reviewer=self._reviewer,
                note=note,
            ),
            strings.DONE_LOCK,
        )

    def unlock_section(self, section_id: str, reason: str) -> None:
        self._change(
            "Unlock section",
            lambda s: self._ws.unlock_section(
                s.session.session_id, section_id, reviewer=self._reviewer, reason=reason
            ),
            strings.DONE_UNLOCK,
        )

    def decide(self, plan_id: str, proposal_id: str, verdict: Verdict, note: str = "") -> None:
        self._change(
            "Decide on proposal",
            lambda s: self._ws.decide_proposal(
                s.session.session_id,
                plan_id,
                proposal_id,
                verdict,
                timeline=s.session.timeline,
                reviewer=self._reviewer,
                note=note,
            ),
            strings.DONE_DECISION,
        )

    def request_export_authorization(self, profile_id: str) -> None:
        """Step 1: read what would be authorized. The view confirms it with the person."""
        snapshot = self._guard()
        if snapshot is None:
            return
        session_id = snapshot.session.session_id
        self._submit(
            "Check export readiness",
            lambda: self._ws.readiness(session_id, profile_id),
            self._on_readiness,
        )

    def authorize_export(self, readiness: ReadinessReport) -> None:
        """Step 2, only after the person confirmed exactly this readiness report."""
        self._change(
            "Authorize final export",
            lambda s: self._ws.authorize_export(
                s.session.session_id,
                timeline=readiness.timeline,
                reviewer=self._reviewer,
                profile_id=readiness.profile_id,
            ),
            None,
        )

    # --- results --------------------------------------------------------------------------------
    def _on_snapshot(self, result: Result[ReviewSnapshot, str]) -> None:
        match result:
            case Ok(snapshot):
                self._set_snapshot(snapshot)
            case Err(message):
                self._set_snapshot(None)
                self.error_changed.emit(message)

    def _on_readiness(self, result: Result[ReadinessReport, str]) -> None:
        match result:
            case Err(message):
                self.error_changed.emit(message)
            case Ok(report) if report.readiness.hard:
                items = "\n".join(f"• {b.message}" for b in report.readiness.hard)
                self.error_changed.emit(strings.EXPORT_BLOCKED.format(items=items))
            case Ok(report) if not report.output_config_hash:
                self.error_changed.emit(strings.EXPORT_PROFILE_UNUSABLE)
            case Ok(report):
                self.confirm_export_requested.emit(report)

    def _set_snapshot(self, snapshot: ReviewSnapshot | None) -> None:
        before = self.can_act
        self._snapshot = snapshot
        self.snapshot_changed.emit(snapshot)
        if self.can_act != before:
            self.can_act_changed.emit(self.can_act)

    # --- plumbing -------------------------------------------------------------------------------
    def _guard(self) -> ReviewSnapshot | None:
        if not self._reviewer:
            self.error_changed.emit(strings.NEED_REVIEWER)
            return None
        if self._snapshot is None:
            self.error_changed.emit(strings.NEED_SESSION)
            return None
        self.error_changed.emit("")
        return self._snapshot

    def _change[T](
        self,
        name: str,
        work: Callable[[ReviewSnapshot], Result[T, str]],
        done_message: str | None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        snapshot = self._guard()
        if snapshot is None:
            return

        def finished(result: Result[T, str]) -> None:
            match result:
                case Ok(value):
                    message = done_message or strings.AUTHORIZED.format(authorization=value)
                    self._status.show(message)
                    if on_done is not None:
                        on_done()
                    self._load_sessions()
                    self.select_session(snapshot.session.session_id)
                case Err(error):
                    self.error_changed.emit(error)

        self._submit(name, lambda: work(snapshot), finished)

    def _submit[T](self, name: str, work: Callable[[], T], on_success: Callable[[T], None]) -> None:
        self._pending += 1
        if self._pending == 1:
            self.busy_changed.emit(True)
        self._jobs.submit(
            name,
            lambda _ctx: work(),
            on_success=on_success,
            on_error=self._on_error,
            on_finished=self._end,
        )

    def _on_error(self, report: ErrorReport) -> None:
        self.error_changed.emit(report.user_message)

    def _end(self) -> None:
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self.busy_changed.emit(False)
