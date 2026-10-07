import os
import sys
import shutil
from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from media_house.bootstrap.application import Application, StartupOptions
from media_house.shared.configuration import Environment
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.video_improvement.application.contracts import VideoImprover, ImproveVideoCommand
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok

class Progress:
    """Prints a line whenever the progress moves by five percent."""

    def __init__(self) -> None:
        self._last = -5

    def report(self, current: int, total: int | None = None, message: str = "") -> None:
        if current - self._last >= 5 or current >= 100:
            self._last = current
            print(f"  [{current:3d}%] {message}", flush=True)


def main():
    app = QApplication(sys.argv)
    
    # 1. Ask user for a file
    file_path, _ = QFileDialog.getOpenFileName(
        None,
        "Select a video file to improve",
        "",
        "Video Files (*.mp4 *.mov *.mkv *.webm *.avi)"
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
        improver = media_app.container.resolve(VideoImprover)
        
        # 3. Import original
        print("Importing into scratch library...")
        imported = library.import_file(Path(file_path))
        if not isinstance(imported, Ok):
            print(f"Failed to import: {imported.error}")
            return
            
        asset_id = imported.value.asset.id
        
        # 4. Improve video
        print("Improving video...")
        cmd = ImproveVideoCommand(
            source_asset_id=asset_id,
            # Which camera: leave None to decide from the file's metadata (a GoPro HERO9 is found by
            # its firmware tag). Other choices: gopro_flat, rec709, studio_camera, sony_slog3, ...
            source_profile=None,
            # How it should look. "studio" suits a controlled indoor talking head, "outdoor" harsh
            # light and action footage, "natural" does the least, "social" is livelier.
            processing_profile="studio",
            # Fine-tuning, one dotted path per value (see domain/settings.py for all of them):
            overrides={
                # "color.min_gamma": 0.70,          # allow a stronger shadow/midtone lift (1 = none)
                # "color.exposure_strength": 1.0,   # correct all of the measured exposure error
                # "color.saturation_max": 0.42,     # pull strong colours in further
                # "color.white_balance_strength": 0.0,  # coloured lighting is intended: leave it
                # "output.crf": 18,                 # smaller and faster, still very high quality
                # "output.preset": "medium",        # slower, a little smaller at the same quality
            },
        )

        result = improver.execute(cmd, JobContext("demo", JobContext.detached().cancellation, Progress()))
        if not isinstance(result, Ok):
            print(f"Failed to improve video: {result.error.user_message}")
            return
            
        provenance = result.value.provenance
        print(
            f"Source profile: {provenance.source_profile} ({provenance.source_profile_origin}: "
            f"{provenance.source_profile_evidence}); processing profile: {provenance.processing_profile}"
        )
        for operation in provenance.operations:
            if operation.status.value == "applied":
                print(f"  applied  {operation.name} {dict(operation.parameters)}: {operation.reason}")
        for warning in provenance.warnings:
            print(f"  warning: {warning}")
        if not result.value.changed:
            print("Video is already perfect, no improvement was necessary!")
            
        derived_id = result.value.asset.id
        
        # 5. Open results
        derived_path = library.local_path(derived_id)
        if isinstance(derived_path, Ok):
            print(f"Success! Improved video saved to: {derived_path.value}")
            
            # Show original and derived using the default media player
            try:
                if hasattr(os, 'startfile'):
                    if result.value.changed:
                        os.startfile(file_path)
                    os.startfile(derived_path.value)
                else:
                    print("os.startfile is not available to play the video.")
            except Exception as e:
                print(f"Could not open media player automatically: {e}")
            
            msg = f"Processed video successfully!\n\nOriginal: {file_path}\nImproved: {derived_path.value}"
            if not result.value.changed:
                msg = f"Video didn't need improvement!\n\nOriginal: {file_path}"
            QMessageBox.information(None, "Success", msg)
        else:
            print("Could not locate derived video path.")
            
    finally:
        media_app.shutdown()

if __name__ == "__main__":
    main()
