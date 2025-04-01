import sys
import ctypes
import traceback
from pathlib import Path

# Imports - Assuming run.bat has installed requirements, these should succeed.
# If they fail here, it indicates a deeper issue (e.g., corrupted install).
from PyQt6.QtWidgets import QApplication, QMessageBox # Keep QMessageBox for error popups
from PyQt6.QtGui import QIcon, QImageReader
from gui.main_window import ImageGallery
from gui.dialogs.requirements_dialog import RequirementsDialog # Keep dialog import
import config
# Removed check_critical_requirements import, no longer used at startup

def main():
    """Main function to set up and run the application."""
    # Set AppUserModelID for Windows taskbar grouping and icon
    # See: https://docs.microsoft.com/en-us/windows/win32/shell/appids
    if sys.platform == "win32":
        myappid = 'com.alexander.arcexplorer.1' # Unique ID for the app
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except AttributeError:
            print("Warning: Could not set AppUserModelID. Taskbar icon might not group correctly.")

    app = QApplication(sys.argv)

    # Set application icon (optional, but good practice)
    if config.ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(config.ICON_PATH)))
    else:
        print(f"Warning: Application icon not found at {config.ICON_PATH}")

    # Remove Qt's image allocation limit if dealing with many large images
    QImageReader.setAllocationLimit(0)

    # --- Create and Show Main Window ---
    # No pre-check here anymore. If ImageGallery import failed earlier,
    # this might raise NameError, but PyQt6 check ensures QApplication runs.
    # If ImageGallery *did* import but its *internal* imports fail (like onnx),
    # the window should still appear, and checks inside will handle it.
    try:
        gallery = ImageGallery()
        gallery.show() # showMaximized() is called within ImageGallery.__init__
    except NameError:
        # This happens if 'ImageGallery' failed to import earlier due to missing deps
        QMessageBox.critical(None, "Initialization Error",
                             "Failed to initialize the main window.\n"
                             "This might be due to missing dependencies like 'onnxruntime' or 'pandas'.\n"
                             "Please use the 'Check Requirements' button (if available) or run 'run.bat'.")
        # Don't start event loop if main window failed
        sys.exit(1)
    except Exception as e:
        # Catch other unexpected errors during gallery initialization
        QMessageBox.critical(None, "Initialization Error",
                             f"An unexpected error occurred during startup:\n{e}\n\n"
                             "Please check the console output for details.")
        print(f"Unexpected error during ImageGallery init: {e}")
        traceback.print_exc()
        sys.exit(1)


    # Start the application event loop (only if gallery was successfully shown)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()