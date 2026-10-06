"""Desktop bootstrap: Qt application, error boundary, theme, main window, event loop."""

import sys
from importlib import resources
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from media_house import __version__
from media_house.bootstrap.application import Application, StartupOptions
from media_house.bootstrap.cli import format_startup_error
from media_house.bootstrap.lifecycle import StartupError, StartupPhase
from media_house.presentation import strings
from media_house.presentation.application_window import MainWindow
from media_house.presentation.core_ui import CoreUiContributor
from media_house.presentation.extension import UiContributor, UiRegistry
from media_house.presentation.qt.dispatcher import UiDispatcher
from media_house.presentation.qt.error_boundary import GlobalErrorBoundary, QtErrorPresenter
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.presentation.qt.window_state import WindowStateStore
from media_house.presentation.styling import ThemeManager
from media_house.shared.concurrency import JobScheduler
from media_house.shared.configuration import Environment
from media_house.shared.errors.handler import ErrorHandler
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class DesktopSession:
    """Everything the running desktop UI owns. Keeps strong references alive."""

    def __init__(self, application: Application, app: QApplication) -> None:
        self.application = application
        container = application.container

        self.dispatcher = UiDispatcher()
        self.status = StatusReporter()
        self.window: MainWindow | None = None

        error_handler = container.resolve(ErrorHandler)
        presenter = QtErrorPresenter(lambda: self.window)
        self.error_boundary = GlobalErrorBoundary(error_handler, presenter, self.dispatcher)
        self.jobs = UiJobRunner(
            container.resolve(JobScheduler),
            self.dispatcher,
            on_unhandled_error=presenter.present,
        )
        # Modules resolve these lazily when building their view models.
        container.register_instance(UiJobRunner, self.jobs)
        container.register_instance(StatusReporter, self.status)

        self.theme = ThemeManager(app, application.settings.ui.theme)
        registry = UiRegistry()
        CoreUiContributor(
            theme=self.theme,
            runner=self.jobs,
            quit_app=app.quit,
            version=__version__,
            parent_provider=lambda: self.window,
        ).contribute(registry)
        for contributor in container.resolve_all(UiContributor):
            contributor.contribute(registry)

        suffix = (
            ""
            if application.settings.environment is Environment.PRODUCTION
            else (f" [{application.settings.environment.value}]")
        )
        state_store = WindowStateStore(application.paths.config_dir / "window-state.ini")
        self.window = MainWindow(
            registry,
            title=f"{strings.APP_TITLE}{suffix}",
            status=self.status,
            state_store=state_store,
        )
        # Ctrl+Q / app.quit() do not always deliver a close event to the window.
        window = self.window
        app.aboutToQuit.connect(lambda: state_store.save(window))
        self.error_boundary.install()

    def close(self) -> None:
        self.error_boundary.uninstall()


def run_desktop(options: StartupOptions) -> int:
    """Start the application, run the Qt event loop, shut down cleanly. Returns the exit code."""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    assert isinstance(app, QApplication)  # noqa: S101 - narrows QCoreApplication for the type checker
    QCoreApplication.setApplicationName("Media-House")
    QCoreApplication.setOrganizationName("Media-House")
    icon = Path(
        str(resources.files("media_house.presentation").joinpath("resources/icons/app.svg"))
    )
    app.setWindowIcon(QIcon(str(icon)))

    try:
        application = Application.start(options)
    except StartupError as error:
        QMessageBox.critical(None, strings.STARTUP_FAILED_TITLE, format_startup_error(error))
        return 2

    session: DesktopSession | None = None
    try:
        try:
            session = DesktopSession(application, app)
        except Exception as exc:  # noqa: BLE001 - startup boundary: report, clean up, exit code 2
            _log.error("UI start failed", exc_info=exc)
            failure = StartupError(StartupPhase.PRESENTATION, exc)
            QMessageBox.critical(None, strings.STARTUP_FAILED_TITLE, format_startup_error(failure))
            return 2
        assert session.window is not None  # noqa: S101
        session.window.show()
        app.aboutToQuit.connect(application.shutdown)
        return app.exec()
    finally:
        if session is not None:
            session.close()
        application.shutdown()
