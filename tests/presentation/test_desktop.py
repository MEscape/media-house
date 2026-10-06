import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMainWindow

from media_house.bootstrap.application import Application, StartupOptions
from media_house.bootstrap.desktop import DesktopSession, run_desktop
from media_house.presentation.extension import UiContributor
from media_house.shared.configuration import Environment

pytestmark = pytest.mark.presentation

# run_desktop() runs QApplication.exec(). Doing that on pytest-qt's shared QApplication leaves
# Qt in its "closing down" state and breaks every later test, so the end-to-end launch runs
# in a real child process instead.
LAUNCH_AND_QUIT = """
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from media_house.bootstrap.application import StartupOptions
from media_house.bootstrap.desktop import run_desktop
from media_house.shared.configuration import Environment

app = QApplication([])
QTimer.singleShot(500, app.quit)
options = StartupOptions(env={}, home=Path(sys.argv[1]), environment=Environment.TEST)
sys.exit(run_desktop(options))
"""


def options(home: Path) -> StartupOptions:
    return StartupOptions(env={}, home=home, environment=Environment.TEST)


def test_desktop_session_assembles_the_shell_from_module_contributions(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    application = Application.start(options(tmp_path))
    session = DesktopSession(application, qapp)
    try:
        assert len(application.container.resolve_all(UiContributor)) == 2
        window = session.window
        assert isinstance(window, QMainWindow)
        assert "[test]" in window.windowTitle()
        menus = [a.text().replace("&", "") for a in window.menuBar().actions()]
        assert menus == ["File", "View", "Media", "Workspace", "Help"]
    finally:
        session.close()
        assert session.window is not None
        session.window.close()
        application.shutdown()


def test_full_desktop_launch_runs_the_event_loop_and_exits_cleanly(tmp_path: Path) -> None:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, test-only
        [sys.executable, "-c", LAUNCH_AND_QUIT, str(tmp_path)],
        env={"QT_QPA_PLATFORM": "offscreen", "PATH": ""},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "config" / "window-state.ini").is_file()  # saved on quit


def test_run_desktop_reports_startup_failure_with_exit_code_2(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(
        "media_house.bootstrap.desktop.QMessageBox.critical",
        lambda _parent, _title, text: shown.append(text),
    )
    bad = StartupOptions(env={"MEDIA_HOUSE_LOGGING__LEVEL": "LOUD"}, home=tmp_path)
    assert run_desktop(bad) == 2
    assert "Reference: ERR-" in shown[0]
