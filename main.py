import sys
import ctypes
import traceback
from pathlib import Path
# CRITICAL: This import MUST come BEFORE PyQt6 imports. Loading onnxruntime
# before PyQt6 ensures correct DLL resolution on Windows. Moving this import
# after PyQt6 will cause "DLL load failed" errors for onnxruntime.
from image_processing.tagger import ImageTaggerModel

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QIcon, QImageReader
from gui.main_window import ImageGallery
from gui.dialogs.requirements_dialog import RequirementsDialog
import config

def main():
    """Main function to set up and run the application."""
    # --- Enforce launch via run.bat ---
    import os
    if os.environ.get("ARCSHELF_LAUNCHED_VIA_BAT") != "1":
        QMessageBox.critical(None, "Launch Error",
            "ArcShelf must be started using 'run.bat'.\n\n"
            "Please close this window and use the provided batch script to launch the application.\n"
            "This ensures all requirements and environment settings are correct."
        )
        sys.exit(1)

    # Set AppUserModelID for Windows taskbar grouping and icon
    if sys.platform == "win32":
        # Changed ID to force icon cache refresh
        myappid = 'com.dmolmar.arcshelf.v1.1'
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except AttributeError:
            print("Warning: Could not set AppUserModelID. Taskbar icon might not group correctly.")

    # Reutiliza la instancia de QApplication si ya existe, si no, crea una nueva.
    app = QApplication.instance() or QApplication(sys.argv)

    # Set application icon
    if config.ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(config.ICON_PATH)))
    else:
        print(f"Warning: Application icon not found at {config.ICON_PATH}")

    # Remove Qt's image allocation limit
    QImageReader.setAllocationLimit(0)

    # --- Create and Show Main Window ---
    try:
        gallery = ImageGallery()
        gallery.show()
    except Exception as e:
        # Catch other unexpected errors during gallery initialization
        QMessageBox.critical(None, "Initialization Error",
                             f"An unexpected error occurred during startup:\n{e}\n\n"
                             "Please check the console output for details.")
        print(f"Unexpected error during ImageGallery init: {e}")
        traceback.print_exc()
        sys.exit(1)


    # Start the application event loop
    sys.exit(app.exec())

if __name__ == "__main__":
    main()