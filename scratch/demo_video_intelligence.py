"""Analyse a video and print what Video Intelligence found.

    uv run python scratch/demo_video_intelligence.py path/to/clip.mp4 [triage|fast|standard|deep]

Runs in a throw-away library under ~/.media_house_scratch. The analysis only measures and
describes: it never changes the video and never recommends anything.
"""

import shutil
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QDialog, QVBoxLayout, QTextEdit, QPushButton

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.video_intelligence.application.contracts import (
    AnalyzeVideoCommand,
    RuntimeConfig,
    VideoAnalyzer,
    analysis_to_json,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok


class AnalysisViewer(QDialog):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Analysis Result")
        self.resize(800, 600)
        
        layout = QVBoxLayout(self)
        
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(text)
        self.text_edit.setReadOnly(True)
        self.text_edit.setStyleSheet("font-family: monospace;")
        layout.addWidget(self.text_edit)
        
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        layout.addWidget(self.btn_close)


class Progress:
    def report(self, current: int, total: int | None = None, message: str = "") -> None:
        print(f"  [{current}/{total}] {message}", flush=True)


def main() -> int:
    profile = "standard"
    if len(sys.argv) >= 2:
        path = Path(sys.argv[1])
        if len(sys.argv) > 2:
            profile = sys.argv[2]
    else:
        app_qt = QApplication.instance() or QApplication(sys.argv)
        file_path, _ = QFileDialog.getOpenFileName(
            None,
            "Select a video file to analyze",
            "",
            "Video files (*.mp4 *.mov *.mkv *.webm *.avi)"
        )
        if not file_path:
            print("No file selected. Exiting.")
            return 0
        path = Path(file_path)

    scratch = Path.home() / ".media_house_scratch"
    shutil.rmtree(scratch, ignore_errors=True)
    app = Application.start(
        StartupOptions(env={}, home=scratch, environment=Environment.DEVELOPMENT)
    )
    try:
        library = app.container.resolve(MediaLibrary)
        imported = library.import_file(path)
        if not isinstance(imported, Ok):
            print(f"Could not import: {imported.error}")
            return 1
        ctx = JobContext.detached()
        ctx = JobContext(ctx.job_id, ctx.cancellation, Progress())
        result = app.container.resolve(VideoAnalyzer).execute(
            AnalyzeVideoCommand(
                imported.value.asset.id, 
                profile,
                runtime=RuntimeConfig(allow_model_download=True)
            ), 
            ctx
        )
        if not isinstance(result, Ok):
            print(f"Analysis failed: {result.error}")
            return 1

        analysis = result.value.analysis
        
        output_lines = []
        output_lines.append(f"{profile}: measured on the {analysis.measured_on.value} footage")
        for use in analysis.inputs_used:
            output_lines.append(f"  input {use.name}: {use.source.value} {use.detail}")
        for report in analysis.analyzers:
            output_lines.append(f"  analyzer {report.analyzer.value}: {report.state.value} ({report.cache.value})")
        for shot in analysis.shots:
            camera, quality = shot.camera, shot.quality
            movement = camera.movement.value if camera.movement else camera.state.value
            output_lines.append(
                f"{shot.shot_id} {shot.range.start.seconds:8.3f}s-{shot.range.end.seconds:8.3f}s "
                f"in={shot.boundary_in.kind.value if shot.boundary_in.kind else '?'} "
                f"camera={movement} reasons={list(camera.reasons) + list(quality.reasons)}"
            )
        for warning in analysis.warnings:
            output_lines.append(f"warning: {warning}")
            
        output_lines.append("\n=== Full Analysis JSON ===")
        output_lines.append(analysis_to_json(analysis))
            
        output_text = "\n".join(output_lines)
        print(f"\n{output_text}")
        
        if 'app_qt' not in locals():
            app_qt = QApplication.instance() or QApplication(sys.argv)
            
        viewer = AnalysisViewer(output_text)
        viewer.exec()
        
        return 0
    finally:
        app.shutdown()


if __name__ == "__main__":
    sys.exit(main())
