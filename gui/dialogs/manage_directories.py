# gui/dialogs/manage_directories.py

import os
import sys
import sqlite3
import datetime
import math
from typing import TYPE_CHECKING, Set, List, Tuple, Optional, Dict, Callable
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter, QWidget, QListWidget,
    QPushButton, QLineEdit, QCheckBox, QSpinBox, QFileDialog, QMessageBox,
    QListWidgetItem, QAbstractItemView, QLabel, QApplication, # Removed QSizePolicy
    QFrame # Import QFrame
)
from PyQt6.QtCore import Qt, QTimer, QThreadPool, QSize, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap, QImage
from PIL import Image, UnidentifiedImageError

# Local imports (within the gui package)
from gui.widgets.directory_list_item import DirectoryListItem

# Imports from other parts of the application package
from database.db_manager import Database
from utils.workers import Worker
# Assuming config might be needed for paths, etc.
import config

# Use TYPE_CHECKING for type hints to avoid circular imports
if TYPE_CHECKING:
    from gui.main_window import ImageGallery # The main application window

# Placeholder for utility function - move later
def human_readable_size(size_bytes: int) -> str:
    """Converts size in bytes to human-readable string."""
    if size_bytes is None or size_bytes < 0: return "N/A" # Handle None
    if size_bytes == 0: return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    try:
        i = int(math.floor(math.log(size_bytes, 1024)))
        i = max(0, min(i, len(size_name) - 1)) # Clamp index
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"
    except (ValueError, OverflowError):
        return f"{size_bytes} B"


class ManageDirectoriesDialog(QDialog):
    """Dialog for managing image directories, processing, and duplicate detection."""

    # Signal to request processing of directories in the main window
    processDirectoriesRequested = pyqtSignal(list)
    # Signal to request deletion of directories in the main window
    deleteDirectoriesRequested = pyqtSignal(list)
    # Signal to update the active directories set in the main window
    activeDirectoriesChanged = pyqtSignal(set)
    # Signal to request reprocessing of specific images
    reprocessImagesRequested = pyqtSignal(list, dict) # list of image_ids, dict of properties to reprocess
    # Signal to update status text in the main window
    updateStatusText = pyqtSignal(str)


    def __init__(self, parent: 'ImageGallery', db: Database, initial_active_directories: Set[str], threadpool: QThreadPool):
        """
        Initializes the ManageDirectoriesDialog.

        Args:
            parent: The main ImageGallery window instance.
            db: The Database manager instance.
            initial_active_directories: The set of currently active directory paths.
            threadpool: The global QThreadPool instance.
        """
        super().__init__(parent)
        self.setWindowTitle("Manage Directories")
        self.db = db
        self.main_window = parent # Keep reference for now, aim to reduce direct calls
        self.active_directories = set(initial_active_directories) # Local copy
        self.threadpool = threadpool
        self.image_paths_in_list: List[Tuple[str, str]] = [] # Store (image_id, path) for the right panel list

        # Set window icon
        icon_path = config.BASE_DIR / "arcueid.ico"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.setup_ui()
        self.load_directories() # Load initial directories

        # Set initial dialog size based on screen
        try:
            screen = QApplication.primaryScreen()
            if screen:
                screen_size = screen.availableGeometry() # Use available geometry
                # Make default size slightly smaller than screenshot suggests relative to screen
                self.resize(int(screen_size.width() * 0.5), int(screen_size.height() * 0.6))
            else:
                 self.resize(900, 600) # Default size if screen info fails
        except Exception as e:
             print(f"Error getting screen size: {e}")
             self.resize(900, 600) # Default size

    def setup_ui(self):
        """Sets up the UI elements of the dialog."""
        self.layout = QHBoxLayout(self)

        # Splitter for left (directories) and right (images/duplicates) panels
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.layout.addWidget(self.splitter)

        # --- Left Panel (Directories) ---
        self.left_panel = QWidget()
        self.left_layout = QVBoxLayout(self.left_panel)
        self.left_layout.setSpacing(10) # Add some vertical spacing

        # Directory List
        self.directory_list = QListWidget()
        self.directory_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # Removed automatic image loading on selection change:
        # self.directory_list.itemSelectionChanged.connect(self.show_selected_images_in_right_panel)
        self.left_layout.addWidget(self.directory_list) # Label removed, list is primary element

        # Directory Selection/Action Buttons
        dir_select_buttons_layout = QHBoxLayout()
        select_all_dir_button = QPushButton("Select All") # Renamed
        select_all_dir_button.clicked.connect(self.directory_list.selectAll)
        unselect_all_dir_button = QPushButton("Unselect All") # Renamed
        unselect_all_dir_button.clicked.connect(self.directory_list.clearSelection)
        self.show_selected_button = QPushButton("Show Selected") # New Button
        self.show_selected_button.clicked.connect(self.show_selected_images_in_right_panel) # Connect new button

        dir_select_buttons_layout.addWidget(select_all_dir_button)
        dir_select_buttons_layout.addWidget(unselect_all_dir_button)
        dir_select_buttons_layout.addWidget(self.show_selected_button) # Add new button
        self.left_layout.addLayout(dir_select_buttons_layout)

        # Separator Line
        line1 = QFrame()
        line1.setFrameShape(QFrame.Shape.HLine)
        line1.setFrameShadow(QFrame.Shadow.Sunken)
        self.left_layout.addWidget(line1)

        # Add Directory Section
        # No group box needed, just layout widgets directly
        browse_layout = QHBoxLayout()
        self.browse_button = QPushButton("Browse") # Renamed
        self.browse_button.clicked.connect(self.select_directory_to_add)
        self.directory_text = QLineEdit() # Make it editable? No, screenshot implies Browse fills it.
        self.directory_text.setPlaceholderText("Select directory path...")
        self.directory_text.setReadOnly(True) # Path filled by browse
        browse_layout.addWidget(self.directory_text)
        browse_layout.addWidget(self.browse_button)
        self.left_layout.addLayout(browse_layout)

        self.add_button = QPushButton("Add") # Renamed
        self.add_button.setToolTip("Add the selected directory to the list of monitored directories.")
        self.add_button.clicked.connect(self.add_directory_action) # Connect to new slot
        # Add button spans width
        self.left_layout.addWidget(self.add_button)

        # Separator Line
        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        line2.setFrameShadow(QFrame.Shadow.Sunken)
        self.left_layout.addWidget(line2)

        # Process/Delete Buttons for selected directories
        process_delete_layout = QHBoxLayout()
        self.process_button = QPushButton("Process") # Renamed from Re-Process Selected
        self.process_button.setToolTip("Scan selected directories for new/modified images and add/update them in the database.")
        self.process_button.clicked.connect(self.process_selected_directories_action) # Connect remains same
        self.delete_button = QPushButton("Delete") # Renamed from Remove Selected
        self.delete_button.setToolTip("Remove selected directories and all associated images from the database (files on disk are NOT deleted).")
        self.delete_button.clicked.connect(self.delete_selected_directories_action)
        process_delete_layout.addWidget(self.process_button)
        process_delete_layout.addWidget(self.delete_button)
        self.left_layout.addLayout(process_delete_layout)

        self.left_layout.addStretch(1) # Add stretch at the bottom

        self.splitter.addWidget(self.left_panel)

        # --- Right Panel (Images / Duplicates) ---
        self.right_panel = QWidget()
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setSpacing(10)

        # Image/Duplicate List
        self.image_list = QListWidget()
        self.image_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.image_list.setWordWrap(True) # Keep word wrap for potentially long info
        self.right_layout.addWidget(self.image_list) # Label removed

        # Image Selection Buttons
        img_buttons_layout = QHBoxLayout()
        select_all_img_button = QPushButton("Select All") # Renamed
        select_all_img_button.clicked.connect(self.image_list.selectAll)
        unselect_all_img_button = QPushButton("Unselect All") # Renamed
        unselect_all_img_button.clicked.connect(self.image_list.clearSelection)
        img_buttons_layout.addWidget(select_all_img_button)
        img_buttons_layout.addWidget(unselect_all_img_button)
        self.right_layout.addLayout(img_buttons_layout)

        # Separator Line
        line3 = QFrame()
        line3.setFrameShape(QFrame.Shape.HLine)
        line3.setFrameShadow(QFrame.Shadow.Sunken)
        self.right_layout.addWidget(line3)

        # Duplicate Detection Section
        dupe_layout_h = QHBoxLayout()
        self.similarity_threshold_label = QLabel("Similarity Threshold (%):") # Adjusted text
        self.similarity_threshold_spinbox = QSpinBox()
        self.similarity_threshold_spinbox.setRange(50, 100)
        self.similarity_threshold_spinbox.setValue(95)
        self.detect_dupes_button = QPushButton("Detect Dupes") # Renamed
        self.detect_dupes_button.clicked.connect(self.detect_dupes_action)
        # Order: Label, Spinbox, Button
        dupe_layout_h.addWidget(self.similarity_threshold_label)
        dupe_layout_h.addWidget(self.similarity_threshold_spinbox)
        dupe_layout_h.addStretch(1) # Push button to the right
        dupe_layout_h.addWidget(self.detect_dupes_button)
        self.right_layout.addLayout(dupe_layout_h)

        # Separator Line
        line4 = QFrame()
        line4.setFrameShape(QFrame.Shape.HLine)
        line4.setFrameShadow(QFrame.Shadow.Sunken)
        self.right_layout.addWidget(line4)

        # Reprocess Section (for selected images)
        self.reprocess_properties_layout = QHBoxLayout() # Layout for checkboxes
        self.reprocess_tags_checkbox = QCheckBox("Tags")
        self.reprocess_thumbnail_checkbox = QCheckBox("Thumbnail")
        self.reprocess_filesize_checkbox = QCheckBox("File Size") # New
        self.reprocess_modtime_checkbox = QCheckBox("Mod Time")  # New
        self.reprocess_resolution_checkbox = QCheckBox("Resolution")# New

        self.reprocess_properties_layout.addWidget(QLabel("Reprocess:")) # Add label
        self.reprocess_properties_layout.addWidget(self.reprocess_tags_checkbox)
        self.reprocess_properties_layout.addWidget(self.reprocess_thumbnail_checkbox)
        self.reprocess_properties_layout.addWidget(self.reprocess_filesize_checkbox)
        self.reprocess_properties_layout.addWidget(self.reprocess_modtime_checkbox)
        self.reprocess_properties_layout.addWidget(self.reprocess_resolution_checkbox)
        self.reprocess_properties_layout.addStretch(1) # Push button to right

        self.reprocess_button = QPushButton("Reprocess Selected") # Renamed
        self.reprocess_button.clicked.connect(self.reprocess_selected_images_action)
        self.reprocess_properties_layout.addWidget(self.reprocess_button)

        self.right_layout.addLayout(self.reprocess_properties_layout)


        self.splitter.addWidget(self.right_panel)

        # Set initial splitter sizes (adjust ratio if needed, e.g., 35% left, 65% right)
        initial_width = self.width()
        self.splitter.setSizes([int(initial_width * 0.35), int(initial_width * 0.65)])

    def load_directories(self):
        """Load unique directory paths from the database and populate the list."""
        print("Loading directories from database...")
        # NOTE: This still loads based on existing images. See thought process notes.
        try:
            with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT path FROM images")
                paths = [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"Error loading directory paths from DB: {e}")
            paths = []

        directories = set()
        for path_str in paths:
            try:
                parent_dir = os.path.dirname(path_str)
                if parent_dir: directories.add(parent_dir)
            except Exception as e: print(f"Error parsing directory from path '{path_str}': {e}")

        self.directory_list.clear()
        self.dir_list_widgets: List[DirectoryListItem] = []

        for directory in sorted(list(directories)):
            is_active = directory in self.active_directories
            item = QListWidgetItem()
            # --- Use DirectoryListItem ---
            # The checkmark in the screenshot is likely the CheckBox within DirectoryListItem
            widget = DirectoryListItem(directory, is_checked=is_active)
            widget.stateChanged.connect(self._handle_directory_state_change)
            item.setSizeHint(widget.sizeHint())
            self.directory_list.addItem(item)
            self.directory_list.setItemWidget(item, widget)
            # --- End Use ---
            self.dir_list_widgets.append(widget)

        print(f"Loaded {len(directories)} unique directories.")
        # Clear right panel initially
        self.image_list.clear()
        self.image_list.addItem("Select directories and click 'Show Selected'.")


    def _handle_directory_state_change(self, directory: str, is_active: bool):
        """Slot to update the internal active_directories set and emit signal."""
        if is_active:
            self.active_directories.add(directory)
        else:
            self.active_directories.discard(directory)
        print(f"Active directories updated: {self.active_directories}")
        self.activeDirectoriesChanged.emit(self.active_directories)
        # Refreshing right panel is now manual via "Show Selected"


    def get_selected_directory_paths(self) -> List[str]:
        """Gets the paths of the directories currently selected in the list view."""
        selected_paths = []
        for item in self.directory_list.selectedItems():
            widget = self.directory_list.itemWidget(item)
            if widget:
                selected_paths.append(widget.getDirectory())
        return selected_paths

    def show_selected_images_in_right_panel(self):
        """Populates the right panel list with images from the selected directories."""
        selected_dirs = self.get_selected_directory_paths()
        self.image_list.clear()
        self.image_paths_in_list.clear()

        if not selected_dirs:
            self.image_list.addItem("Select directories on the left and click 'Show Selected'.")
            return

        print(f"Showing images for selected directories: {selected_dirs}")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor) # Indicate loading
        self.set_ui_enabled(False)
        try:
            with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                all_rows = []
                dir_placeholders = ', '.join('?' for _ in selected_dirs)
                like_conditions = ' OR '.join(['path LIKE ?' for _ in selected_dirs])
                params = [f"{d}%" if not d.endswith('/') else f"{d}%" for d in selected_dirs] # Ensure ends with %

                # Combine conditions
                query = f"""
                    SELECT id, path, file_size, modification_time, resolution
                    FROM images
                    WHERE {like_conditions}
                    ORDER BY path ASC
                """
                cursor.execute(query, params)
                all_rows = cursor.fetchall() # Fetch all matching rows

            if not all_rows:
                 self.image_list.addItem("No images found in the selected directories.")
                 return

            # Populate list
            for row in all_rows:
                image_id, path, file_size, mod_time, resolution = row
                size_str = human_readable_size(file_size if file_size else 0)
                try: date_str = datetime.datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M:%S') if mod_time else "N/A" # Added seconds
                except ValueError: date_str = "Invalid Date"

                # Format like the screenshot: ID, Path, Size, Date, Resolution
                item_text = (
                    f"ID: {image_id}\n"
                    f"Path: {path}\n"
                    f"Size: {size_str}\n"
                    f"Date: {date_str}\n"
                    f"Resolution: {resolution or 'N/A'}"
                )
                list_item = QListWidgetItem(item_text)
                list_item.setData(Qt.ItemDataRole.UserRole, image_id) # Store ID
                self.image_list.addItem(list_item)
                self.image_paths_in_list.append((image_id, path))

        except sqlite3.Error as e:
            print(f"Error fetching images for right panel: {e}")
            self.image_list.addItem(f"Error loading images: {e}")
        finally:
            self.set_ui_enabled(True)
            QApplication.restoreOverrideCursor()


    def select_directory_to_add(self):
        """Opens a dialog to select a directory to add."""
        start_dir = os.path.dirname(self.directory_text.text()) if self.directory_text.text() else str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "Select Directory", start_dir) # Simplified title
        if directory:
            print(f"Directory selected to add: {directory}")
            self.directory_text.setText(self.db.normalize_path(directory))

    # Renamed from add_and_process_directory
    def add_directory_action(self):
        """Adds the selected directory to the list visually."""
        directory = self.directory_text.text().strip()
        if not directory:
            QMessageBox.warning(self, "No Directory", "Please select a directory using 'Browse' first.")
            return

        if not os.path.isdir(directory):
            QMessageBox.warning(self, "Invalid Directory", f"The selected path is not a valid directory:\n{directory}")
            return

        normalized_directory = self.db.normalize_path(directory)
        # Check if already in the list
        for i in range(self.directory_list.count()):
            widget = self.directory_list.itemWidget(self.directory_list.item(i))
            if widget and widget.getDirectory() == normalized_directory:
                QMessageBox.information(self, "Already Added", "This directory is already in the list.")
                return

        # Add to the list visually
        item = QListWidgetItem()
        widget = DirectoryListItem(normalized_directory, is_checked=True) # Add as active
        widget.stateChanged.connect(self._handle_directory_state_change)
        item.setSizeHint(widget.sizeHint())
        self.directory_list.addItem(item)
        self.directory_list.setItemWidget(item, widget)
        self.dir_list_widgets.append(widget)

        # Update internal active set and emit change
        self.active_directories.add(normalized_directory)
        self.activeDirectoriesChanged.emit(self.active_directories)

        # Clear the text box
        self.directory_text.clear()

        # DO NOT trigger processing here automatically
        # self.processDirectoriesRequested.emit([normalized_directory]) # Removed

        QMessageBox.information(self, "Directory Added", f"Directory added to the list:\n{normalized_directory}\n\nSelect it and click 'Process' to scan for images.")


    def process_selected_directories_action(self):
        """Emits signal to process selected directories."""
        selected_dirs = self.get_selected_directory_paths()
        if not selected_dirs:
            QMessageBox.warning(self, "No Selection", "Please select directories from the list to process.")
            return

        print(f"Requesting processing for selected directories: {selected_dirs}")
        self.processDirectoriesRequested.emit(selected_dirs)
        QMessageBox.information(self, "Processing Started", f"Processing started for {len(selected_dirs)} selected directories.")


    def delete_selected_directories_action(self):
        """Confirms and emits signal to delete selected directories."""
        selected_dirs = self.get_selected_directory_paths()
        if not selected_dirs:
            QMessageBox.warning(self, "No Selection", "Please select directories from the list to delete.")
            return

        reply = QMessageBox.question(
            self, "Confirm Deletion", # Renamed
            f"Are you sure you want to delete the following directories and all their images from the database?\n\n" + "\n".join(selected_dirs) +
            f"\n\n(This only removes database entries, NOT the actual files on disk.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            print(f"Requesting deletion for selected directories: {selected_dirs}")
            self.deleteDirectoriesRequested.emit(selected_dirs)
            # Remove from the list view immediately
            items_to_remove = []
            widgets_to_remove = []
            for i in range(self.directory_list.count()):
                 item = self.directory_list.item(i)
                 widget = self.directory_list.itemWidget(item)
                 if widget and widget.getDirectory() in selected_dirs:
                      items_to_remove.append(item)
                      widgets_to_remove.append(widget)
                      self.active_directories.discard(widget.getDirectory())

            for item in items_to_remove:
                 self.directory_list.takeItem(self.directory_list.row(item))
            for widget in widgets_to_remove:
                 if widget in self.dir_list_widgets: self.dir_list_widgets.remove(widget)
                 widget.deleteLater()

            self.activeDirectoriesChanged.emit(self.active_directories)
            # Clear right panel if deleted dirs were shown
            self.image_list.clear()
            self.image_list.addItem("Select directories and click 'Show Selected'.")

            QMessageBox.information(self, "Deletion Requested", f"Deletion requested for {len(selected_dirs)} directories.")


    def reprocess_selected_images_action(self):
        """Emits signal to reprocess selected images based on checked properties."""
        selected_list_items = self.image_list.selectedItems()
        if not selected_list_items:
            QMessageBox.warning(self, "No Selection", "Please select images from the list to reprocess.")
            return

        image_ids_to_reprocess = [item.data(Qt.ItemDataRole.UserRole) for item in selected_list_items if item.data(Qt.ItemDataRole.UserRole)]
        if not image_ids_to_reprocess:
             QMessageBox.warning(self, "Error", "Could not retrieve IDs for selected images.")
             return

        # Update properties dict based on new checkboxes
        properties_to_reprocess = {
            "tags": self.reprocess_tags_checkbox.isChecked(),
            "thumbnail": self.reprocess_thumbnail_checkbox.isChecked(),
            "file_size": self.reprocess_filesize_checkbox.isChecked(), # New
            "mod_time": self.reprocess_modtime_checkbox.isChecked(), # New
            "resolution": self.reprocess_resolution_checkbox.isChecked(), # New
        }

        if not any(properties_to_reprocess.values()):
            QMessageBox.warning(self, "No Properties Selected", "Please check at least one property (Tags, Thumbnail, File Size, etc.) to reprocess.")
            return

        print(f"Requesting reprocessing for {len(image_ids_to_reprocess)} images. Properties: {properties_to_reprocess}")
        self.reprocessImagesRequested.emit(image_ids_to_reprocess, properties_to_reprocess)
        QMessageBox.information(self, "Reprocessing Started", f"Reprocessing started for {len(image_ids_to_reprocess)} selected images.")


    # --- Duplicate Detection ---
    # (Keep detect_dupes_action, compare_image_tags, on_detection_finished,
    #  on_detection_error, add_dupe_pair_to_gui, populate_dupe_section,
    #  get_cached_thumbnail as they are, unless specific bugs are found)
    # ---
    def detect_dupes_action(self):
        """Starts the duplicate detection process."""
        print("detect_dupes_action: Starting duplicate detection")
        self.image_list.clear() # Clear the right panel for results
        self.image_list.addItem("Starting duplicate detection...")

        self.set_ui_enabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        similarity_threshold = self.similarity_threshold_spinbox.value() / 100.0
        print(f"detect_dupes_action: Similarity threshold: {similarity_threshold}")

        dirs_to_search = list(self.active_directories) # Use active directories
        if not dirs_to_search:
            QMessageBox.information(self, "No Active Directories", "Please ensure at least one directory is checked as active to search for duplicates.")
            self.set_ui_enabled(True)
            QApplication.restoreOverrideCursor()
            self.image_list.clear()
            self.image_list.addItem("Select directories and click 'Show Selected'.")
            return

        print(f"detect_dupes_action: Searching in active directories: {dirs_to_search}")

        image_paths_in_scope = []
        try:
            with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                dir_placeholders = ', '.join('?' for _ in dirs_to_search)
                like_conditions = ' OR '.join(['path LIKE ?' for _ in dirs_to_search])
                params = [f"{d}%" if not d.endswith('/') else f"{d}%" for d in dirs_to_search]
                query = f"SELECT path FROM images WHERE {like_conditions}"
                cursor.execute(query, params)
                image_paths_in_scope.extend([row[0] for row in cursor.fetchall()])
            print(f"detect_dupes_action: Found {len(image_paths_in_scope)} images in scope.")
        except sqlite3.Error as e:
             QMessageBox.critical(self, "Database Error", f"Error fetching images for duplicate check: {e}")
             self.set_ui_enabled(True)
             QApplication.restoreOverrideCursor()
             self.image_list.clear()
             self.image_list.addItem("Select directories and click 'Show Selected'.")
             return

        if len(image_paths_in_scope) < 2:
            QMessageBox.information(self, "Not Enough Images", "Need at least two images in the active directories to compare.")
            self.set_ui_enabled(True)
            QApplication.restoreOverrideCursor()
            self.image_list.clear()
            self.image_list.addItem("Need at least two images to compare.")
            return

        worker = Worker(self.compare_image_tags, image_paths=image_paths_in_scope, similarity_threshold=similarity_threshold, status_callback=self.updateStatusText.emit)
        worker.signals.finished.connect(self.on_detection_finished)
        worker.signals.error.connect(self.on_detection_error)
        # Connect progress signal if compare_image_tags provides it
        # worker.signals.progress.connect(self.update_progress_display)
        # worker.signals.update_info_text.connect(self.updateStatusText.emit) # Already passed as kwarg

        self.threadpool.start(worker)
        print("detect_dupes_action: Comparison worker started.")
        self.image_list.clear()
        self.image_list.addItem("Duplicate detection running in background...")


    def compare_image_tags(self, image_paths: List[str], similarity_threshold: float, status_callback: Optional[Callable[[str], None]] = None) -> List[Tuple[str, str, float]]:
        """
        Compares images based on Jaccard similarity of their tags.
        (Keep implementation as is)
        """
        print(f"compare_image_tags: Starting comparison for {len(image_paths)} images, threshold {similarity_threshold}")
        if len(image_paths) < 2: return []

        image_tags_map: Dict[str, Set[str]] = {} # path -> set(tags)
        try:
            with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                chunk_size = 500 # Process paths in chunks if list is very large
                all_rows = []
                for i in range(0, len(image_paths), chunk_size):
                     chunk = image_paths[i:i+chunk_size]
                     path_placeholders = ','.join('?' for _ in chunk)
                     cursor.execute(f"""
                         SELECT i.path, t.name
                         FROM images i
                         JOIN image_tags it ON i.id = it.image_id
                         JOIN tags t ON it.tag_id = t.id
                         WHERE i.path IN ({path_placeholders})
                     """, chunk)
                     all_rows.extend(cursor.fetchall())

                for path, tag_name in all_rows:
                    if path not in image_tags_map: image_tags_map[path] = set()
                    image_tags_map[path].add(tag_name)
            print(f"compare_image_tags: Fetched tags for {len(image_tags_map)} images.")
        except sqlite3.Error as e:
            print(f"Database error fetching tags for comparison: {e}")
            if status_callback: status_callback(f"Error fetching tags: {e}")
            return []

        comparison_results: List[Tuple[str, str, float]] = []
        path_list = list(image_tags_map.keys())
        n = len(path_list)
        total_comparisons = n * (n - 1) // 2
        completed_comparisons = 0
        last_status_update_time = datetime.datetime.now().timestamp()
        update_interval_seconds = 1.0

        print(f"compare_image_tags: Starting {total_comparisons} pairwise comparisons...\n")
        if status_callback: status_callback(f"Comparing {n} images ({total_comparisons:,} pairs)...\n")

        for i in range(n):
            path1 = path_list[i]; tags1 = image_tags_map[path1]
            if not tags1: continue
            for j in range(i + 1, n):
                path2 = path_list[j]; tags2 = image_tags_map.get(path2)
                if not tags2: continue
                intersection = len(tags1.intersection(tags2)); union = len(tags1.union(tags2))
                similarity = intersection / union if union > 0 else 0.0
                if similarity >= similarity_threshold: comparison_results.append((path1, path2, similarity))
                completed_comparisons += 1
                current_time = datetime.datetime.now().timestamp()
                if status_callback and (current_time - last_status_update_time >= update_interval_seconds or completed_comparisons == total_comparisons):
                    progress_percentage = (completed_comparisons / total_comparisons) * 100 if total_comparisons > 0 else 100
                    status_callback(f"Comparing: {completed_comparisons:,}/{total_comparisons:,} pairs ({progress_percentage:.1f}%)\n")
                    last_status_update_time = current_time

        comparison_results.sort(key=lambda x: x[2], reverse=True)
        print(f"compare_image_tags: Found {len(comparison_results)} pairs above threshold.")
        if status_callback: status_callback(f"Comparison complete. Found {len(comparison_results)} potential duplicate pairs.\n")
        return comparison_results


    def on_detection_finished(self, comparison_results: List[Tuple[str, str, float]]):
        """Handles the results from the duplicate detection worker."""
        print(f"on_detection_finished: Received {len(comparison_results)} pairs.")
        self.set_ui_enabled(True)
        QApplication.restoreOverrideCursor()
        self.image_list.clear()

        if not comparison_results:
            self.image_list.addItem("No similar image pairs found above the threshold.\n")
            self.updateStatusText.emit("Duplicate detection complete: No pairs found.\n")
            return

        self.updateStatusText.emit(f"Duplicate detection complete: Found {len(comparison_results)} pairs. Populating list...\n")
        for pair_data in comparison_results: self.add_dupe_pair_to_gui(pair_data)
        self.updateStatusText.emit(f"Duplicate detection complete: Displaying {len(comparison_results)} pairs.\n")


    def on_detection_error(self, error_info: tuple):
        """Handles errors reported by the duplicate detection worker."""
        exception, traceback_str = error_info
        print(f"Duplicate detection worker failed: {exception}", file=sys.stderr)
        self.set_ui_enabled(True)
        QApplication.restoreOverrideCursor()
        self.image_list.clear()
        self.image_list.addItem(f"Error during duplicate detection:\n{exception}")
        QMessageBox.critical(self, "Detection Error", f"An error occurred during duplicate detection:\n{exception}")
        self.updateStatusText.emit(f"Duplicate detection failed: {exception}\n")

    def _get_dupe_image_info(self, path: str, thumb_height: int) -> Tuple[Optional[QPixmap], Dict[str, str]]:
        """
        Helper to load thumbnail and structured info for one image in a duplicate pair.

        Returns:
            Tuple: (Scaled QPixmap or None, Dictionary containing 'Filename', 'Size', 'Resolution', 'Date')
        """
        scaled_pixmap = None
        info = {"Filename": "N/A", "Size": "N/A", "Resolution": "N/A", "Date": "N/A"}

        image_id = self.db.get_image_id_from_path(path)
        raw_pixmap = self.get_cached_thumbnail(image_id) if image_id else None

        if raw_pixmap:
            scaled_pixmap = raw_pixmap.scaledToHeight(thumb_height, Qt.TransformationMode.SmoothTransformation)
            # Set fixed width based on scaled height to maintain aspect ratio
            # scaled_pixmap = scaled_pixmap.scaledToWidth(scaled_pixmap.width(), Qt.TransformationMode.SmoothTransformation)

        info["Filename"] = os.path.basename(path)
        res_str = "N/A"
        try:
            file_exists = Path(path).is_file()
            if file_exists:
                size = os.path.getsize(path)
                mod_time = os.path.getmtime(path)
                info["Size"] = human_readable_size(size)
                try:
                    info["Date"] = datetime.datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    info["Date"] = "Invalid Date"

                # Get resolution (try DB first, then PIL)
                db_res = self.db.get_resolutions_for_paths([path]).get(path)
                if db_res:
                    res_str = db_res
                else:
                    try:
                        with Image.open(path) as img:
                            res_str = f"{img.width}x{img.height}"
                    except Exception:
                        pass # Keep res_str as "N/A" if PIL fails
                info["Resolution"] = res_str
            else:
                info["Filename"] += " (Not Found)"

        except Exception as e:
            print(f"Error getting info for {path}: {e}")
            info["Filename"] += " (Error)"

        return scaled_pixmap, info

    def add_dupe_pair_to_gui(self, pair_data: Tuple[str, str, float]):
        """Creates and adds a custom widget for a duplicate pair to the list (two-column layout)."""
        try:
            path1, path2, similarity = pair_data
        except (ValueError, TypeError) as e:
            print(f"Error unpacking pair data in add_dupe_pair_to_gui: {e}, Data: {pair_data}")
            return

        thumb_height = 80 # Adjust thumbnail height if needed

        # --- Get Info and Pixmaps for both images ---
        pixmap1, info1 = self._get_dupe_image_info(path1, thumb_height)
        pixmap2, info2 = self._get_dupe_image_info(path2, thumb_height)

        # --- Create main widget and layout for the list item ---
        list_item_widget = QWidget()
        main_layout = QHBoxLayout(list_item_widget)
        main_layout.setContentsMargins(5, 5, 5, 5) # Overall padding
        main_layout.setSpacing(10) # Space between image column and info column

        # --- Column 1: Images Side-by-Side ---
        images_container = QWidget()
        images_layout = QHBoxLayout(images_container)
        images_layout.setContentsMargins(0, 0, 0, 0)
        images_layout.setSpacing(4) # Small gap between thumbnails

        thumb_label1 = QLabel()
        thumb_label1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if pixmap1:
            thumb_label1.setPixmap(pixmap1)
            thumb_label1.setFixedSize(pixmap1.size()) # Fix size to scaled pixmap
        else:
            thumb_label1.setText("No Thumb")
            thumb_label1.setFixedSize(int(thumb_height * 1.0), thumb_height) # Approx square fallback

        thumb_label2 = QLabel()
        thumb_label2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if pixmap2:
            thumb_label2.setPixmap(pixmap2)
            thumb_label2.setFixedSize(pixmap2.size()) # Fix size to scaled pixmap
        else:
            thumb_label2.setText("No Thumb")
            thumb_label2.setFixedSize(int(thumb_height * 1.0), thumb_height) # Approx square fallback

        images_layout.addWidget(thumb_label1)
        images_layout.addWidget(thumb_label2)
        images_layout.addStretch(1) # Push images to the left if container wider

        # --- Column 2: Combined Information ---
        info_label = QLabel()
        info_label.setWordWrap(True)
        info_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft) # Align text top-left

        # Construct the info text block
        info_text = (
            f"{info1['Filename']}\n"
            f"{info1['Size']}, {info1['Resolution']}, {info1['Date']}\n" # Combine Size/Res/Date
            f"{'-'*20}\n" # Simple separator
            f"{info2['Filename']}\n"
            f"{info2['Size']}, {info2['Resolution']}, {info2['Date']}\n" # Combine Size/Res/Date
            f"{'-'*20}\n" # Simple separator
            f"Similarity: {similarity * 100:.2f}%" # Similarity at the end
        )
        info_label.setText(info_text)

        # --- Add Columns to Main Layout ---
        main_layout.addWidget(images_container, 0) # Image column takes minimum space (stretch factor 0)
        main_layout.addWidget(info_label, 1)       # Info column takes remaining space (stretch factor 1)

        # --- Add to QListWidget ---
        list_item = QListWidgetItem()
        # Calculate a reasonable size hint - might need adjustment
        hint_width = images_container.sizeHint().width() + info_label.sizeHint().width() + main_layout.spacing()
        hint_height = max(images_container.sizeHint().height(), info_label.sizeHint().height()) + 10 # Add padding
        list_item.setSizeHint(QSize(hint_width, hint_height)) # Set size hint

        # Store paths in the item data for potential actions later
        list_item.setData(Qt.ItemDataRole.UserRole, {"path1": path1, "path2": path2})
        self.image_list.addItem(list_item)
        self.image_list.setItemWidget(list_item, list_item_widget)


    def get_cached_thumbnail(self, image_id: Optional[str]) -> Optional[QPixmap]:
        """Safely retrieves a QPixmap thumbnail from the cache."""
        if image_id and hasattr(self.main_window, 'thumbnail_cache'):
            qimage = self.main_window.thumbnail_cache.get_thumbnail(image_id)
            if qimage and not qimage.isNull(): return QPixmap.fromImage(qimage)
        return None

    def set_ui_enabled(self, enabled: bool):
        """Enables or disables relevant UI elements during long operations."""
        self.add_button.setEnabled(enabled)
        self.process_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        self.reprocess_button.setEnabled(enabled)
        self.detect_dupes_button.setEnabled(enabled)
        self.browse_button.setEnabled(enabled)
        self.show_selected_button.setEnabled(enabled)
        self.directory_list.setEnabled(enabled)
        self.image_list.setEnabled(enabled)
        # Reprocess checkboxes
        self.reprocess_tags_checkbox.setEnabled(enabled)
        self.reprocess_thumbnail_checkbox.setEnabled(enabled)
        self.reprocess_filesize_checkbox.setEnabled(enabled)
        self.reprocess_modtime_checkbox.setEnabled(enabled)
        self.reprocess_resolution_checkbox.setEnabled(enabled)
        # Similarity spinbox
        self.similarity_threshold_spinbox.setEnabled(enabled)