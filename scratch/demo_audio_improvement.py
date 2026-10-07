import os
import sys
import shutil
from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from media_house.bootstrap.application import Application, StartupOptions
from media_house.shared.configuration import Environment
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.audio_improvement.application.contracts import AudioImprover, ImproveAudioCommand
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok

def main():
    app = QApplication(sys.argv)
    
    # 1. Ask user for a file
    file_path, _ = QFileDialog.getOpenFileName(
        None,
        "Select an audio or video file to improve",
        "",
        "Audio and video (*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.mp4 *.mov *.mkv *.webm)"
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
        improver = media_app.container.resolve(AudioImprover)
        
        # 3. Import original
        print("Importing into scratch library...")
        imported = library.import_file(Path(file_path))
        if not isinstance(imported, Ok):
            print(f"Failed to import: {imported.error}")
            return
            
        asset_id = imported.value.asset.id
        
        # 4. Improve audio
        print("Improving audio (profile: youtube)...")
        cmd = ImproveAudioCommand(source_asset_id=asset_id, profile="youtube")
        
        result = improver.execute(cmd, JobContext.detached())
        if not isinstance(result, Ok):
            print(f"Failed to improve: {result.error}")
            return
            
        derived_id = result.value.asset.id
        
        # 5. Open results
        derived_path = library.local_path(derived_id)
        if isinstance(derived_path, Ok):
            print(f"Success! Improved audio saved to: {derived_path.value}")
            
            # Show original and derived using the default media player
            try:
                if hasattr(os, 'startfile'):
                    os.startfile(file_path)
                    os.startfile(derived_path.value)
                else:
                    print("os.startfile is not available to play the audio.")
            except Exception as e:
                print(f"Could not open media player automatically: {e}")
            
            QMessageBox.information(None, "Success", f"Processed audio successfully!\n\nOriginal: {file_path}\nImproved: {derived_path.value}")
        else:
            print("Could not locate derived audio path.")
            
    finally:
        media_app.shutdown()

if __name__ == "__main__":
    main()
