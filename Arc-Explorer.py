import sys
import ctypes
from pathlib import Path

# Ensure the 'arc_explorer' package directory is in the Python path
# This might not be strictly necessary if running from the project root,
# but it helps ensure imports work correctly.

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon, QImageReader

# Import the main application window class
from gui.main_window import ImageGallery
import config

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

    # Create and show the main window
    gallery = ImageGallery()
    gallery.show() # showMaximized() is called within ImageGallery.__init__

    # Start the application event loop
    sys.exit(app.exec())

if __name__ == "__main__":
    main()