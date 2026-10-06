import sys
from pathlib import Path
import shutil

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QDialog, QVBoxLayout, QTextEdit, QPushButton

from media_house.bootstrap.application import Application, StartupOptions
from media_house.shared.configuration import Environment
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.audio_intelligence.application.contracts import (
    AudioAnalyzer, 
    AnalyzeAudioCommand, 
    AudioIntelligenceConfig,
    TranscriptionConfig,
    timeline_to_json
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok

class TranscriptViewer(QDialog):
    def __init__(self, transcript_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transcription Result")
        self.resize(600, 400)
        
        layout = QVBoxLayout(self)
        
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(transcript_text)
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
        "Select a media file to transcribe",
        "",
        "Media Files (*.mp3 *.wav *.mp4 *.mkv *.mov *.flac)"
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
        analyzer = media_app.container.resolve(AudioAnalyzer)
        
        # 3. Import original
        print("Importing into scratch library...")
        imported = library.import_file(Path(file_path))
        if not isinstance(imported, Ok):
            print(f"Failed to import: {imported.error}")
            return
            
        asset_id = imported.value.asset.id
        
        # 4. Analyze audio
        print("Analyzing media (this may take a while as models are loaded)...")
        config = AudioIntelligenceConfig(
            transcription=TranscriptionConfig(language="de")
        )
        cmd = AnalyzeAudioCommand(source_asset_id=asset_id, config=config)
        
        result = analyzer.execute(cmd, JobContext.detached())
        if not isinstance(result, Ok):
            print(f"Failed to analyze: {result.error}")
            return
            
        timeline = result.value.timeline
        
        print(f"Success! Detected language: {timeline.language}")
        print(f"Duration: {timeline.duration:.2f}s")
        print(f"Words: {len(timeline.words)}")
        
        # Build text output combining transcript and timestamps
        lines = []
        lines.append("=== Transcript with Timestamps ===")
        for segment in timeline.transcript.segments:
            lines.append(f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}")
            
        lines.append("\n=== Full Analysis JSON ===")
        lines.append(timeline_to_json(timeline))
        
        output_text = "\n".join(lines)
        
        # 5. Open results in a UI window
        viewer = TranscriptViewer(output_text)
        viewer.exec()
            
    finally:
        media_app.shutdown()

if __name__ == "__main__":
    main()
