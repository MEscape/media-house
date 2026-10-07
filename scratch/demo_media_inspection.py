import sys
import shutil
from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QDialog, QVBoxLayout, QTextEdit, QPushButton

from media_house.bootstrap.application import Application, StartupOptions
from media_house.shared.configuration import Environment
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_inspection.application.contracts import MediaInspector, InspectMediaCommand
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok

class InspectionViewer(QDialog):
    def __init__(self, inspection_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Media Inspection Result")
        self.resize(600, 400)
        
        layout = QVBoxLayout(self)
        
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(inspection_text)
        self.text_edit.setReadOnly(True)
        layout.addWidget(self.text_edit)
        
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        layout.addWidget(self.btn_close)

def main():
    app = QApplication(sys.argv)
    
    # 1. Ask user for a file
    file_path, _ = QFileDialog.getOpenFileName(
        None,
        "Select an audio or video file to inspect",
        "",
        "Media files (*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.mp4 *.mov *.mkv *.webm *.avi)"
    )
    
    if not file_path:
        print("No file selected. Exiting.")
        return

    print(f"Selected: {file_path}")
    
    # 2. Boot up the app container in a scratch directory
    scratch_dir = Path.home() / ".media_house_scratch"
    shutil.rmtree(scratch_dir, ignore_errors=True)
    
    options = StartupOptions(env={}, home=scratch_dir, environment=Environment.DEVELOPMENT)
    media_app = Application.start(options)
    
    try:
        library = media_app.container.resolve(MediaLibrary)
        inspector = media_app.container.resolve(MediaInspector)
        
        # 3. Import original
        print("Importing into scratch library...")
        imported = library.import_file(Path(file_path))
        if not isinstance(imported, Ok):
            print(f"Failed to import: {imported.error}")
            return
            
        asset_id = imported.value.asset.id
        
        # 4. Inspect media
        print("Inspecting media (this will probe the file)...")
        cmd = InspectMediaCommand(source_asset_id=asset_id)
        
        result = inspector.execute(cmd, JobContext.detached())
        if not isinstance(result, Ok):
            print(f"Failed to inspect: {result.error}")
            return
            
        inspection = result.value.inspection
        
        # Build text output
        lines = []
        lines.append(f"=== Inspection Status ===")
        lines.append(f"Verdict: {inspection.status.verdict.value}")
        lines.append(f"Usable: {inspection.status.usable}")
        if inspection.status.concerns:
            lines.append(f"Concerns: {', '.join(c.value for c in inspection.status.concerns)}")
        
        lines.append("\n=== Container Info ===")
        lines.append(f"Formats: {', '.join(inspection.container.format_names)}")
        if inspection.container.duration is not None:
            lines.append(f"Duration: {inspection.container.duration:.2f}s")
        lines.append(f"Size: {inspection.container.size_bytes} bytes")
        
        lines.append("\n=== Video Streams ===")
        if not inspection.video_streams:
            lines.append("None")
        for i, vs in enumerate(inspection.video_streams):
            lines.append(f"[{i}] {vs.codec} ({vs.geometry.width}x{vs.geometry.height})")
            
        lines.append("\n=== Audio Streams ===")
        if not inspection.audio_streams:
            lines.append("None")
        for i, ast in enumerate(inspection.audio_streams):
            lines.append(f"[{i}] {ast.codec} - {ast.channels} channels @ {ast.sample_rate}Hz")
            
        lines.append("\n=== Findings ===")
        if not inspection.findings:
            lines.append("No findings.")
        for f in inspection.findings:
            lines.append(f"- [{f.severity.value.upper()}] {f.message} (Code: {f.code})")
        
        output_text = "\n".join(lines)
        
        # 5. Open results in a UI window
        viewer = InspectionViewer(output_text)
        viewer.exec()
            
    finally:
        media_app.shutdown()

if __name__ == "__main__":
    main()
