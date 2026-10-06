import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PIL import Image

from media_house.bootstrap.application import Application, StartupOptions
from media_house.shared.configuration import Environment
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.image_adjustment.application.contracts import ImageAdjuster
from media_house.modules.image_adjustment.application.adjust_image import AdjustImageCommand
from media_house.modules.image_adjustment.domain.values import AdjustmentSettings, Glow, HexColor, Canvas, Centering
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok

def main():
    app = QApplication(sys.argv)
    
    # 1. Ask user for a file
    file_path, _ = QFileDialog.getOpenFileName(
        None,
        "Select a real image to process",
        "",
        "Image Files (*.png *.jpg *.jpeg *.webp)"
    )
    
    if not file_path:
        print("No file selected. Exiting.")
        return

    print(f"Selected: {file_path}")
    
    # 2. Boot up the app container in a scratch directory
    import shutil
    scratch_dir = Path.home() / ".media_house_scratch"
    shutil.rmtree(scratch_dir, ignore_errors=True)
    
    options = StartupOptions(env={}, home=scratch_dir, environment=Environment.DEVELOPMENT)
    media_app = Application.start(options)
    
    try:
        library = media_app.container.resolve(MediaLibrary)
        adjuster = media_app.container.resolve(ImageAdjuster)
        
        # 3. Import original
        print("Importing into scratch library...")
        imported = library.import_file(Path(file_path))
        if not isinstance(imported, Ok):
            print(f"Failed to import: {imported.error}")
            return
            
        asset_id = imported.value.asset.id
        
        # 4. Adjust image
        print("Applying background removal, centering, and pink glow...")
        settings = AdjustmentSettings(
            glow=Glow(enabled=True, color=HexColor("#FF2A5F"), opacity=0.85, radius=30, spread=12),
            canvas=Canvas(width=1080, height=1080, padding=60, centering=Centering.CENTER)
        )
        cmd = AdjustImageCommand(source_asset_id=asset_id, settings=settings)
        
        result = adjuster.execute(cmd, JobContext.detached())
        if not isinstance(result, Ok):
            print(f"Failed to adjust: {result.error}")
            return
            
        derived_id = result.value.asset.id
        
        # 5. Open results
        derived_path = library.local_path(derived_id)
        if isinstance(derived_path, Ok):
            print(f"Success! Adjusted image saved to: {derived_path.value}")
            
            # Show original and derived using the default image viewer
            try:
                Image.open(file_path).show(title="Original")
                Image.open(derived_path.value).show(title="Adjusted")
            except Exception as e:
                print(f"Could not open image viewer automatically: {e}")
            
            QMessageBox.information(None, "Success", f"Processed image successfully!\n\nOriginal: {file_path}\nAdjusted: {derived_path.value}")
        else:
            print("Could not locate derived image path.")
            
    finally:
        media_app.shutdown()

if __name__ == "__main__":
    main()
