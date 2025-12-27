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
    QListWidgetItem, QAbstractItemView, QLabel, QApplication,
    QFrame, QMenu
)
from PyQt6.QtCore import Qt, QTimer, QThreadPool, QSize, pyqtSignal, QUrl
from PyQt6.QtGui import QIcon, QPixmap, QImage, QAction, QDesktopServices
from PIL import Image, UnidentifiedImageError

# Local imports (within the gui package)
from gui.widgets.directory_list_item import DirectoryListItem

# Imports from other parts of the application package
from database.db_manager import Database
from utils.workers import Worker
# Assuming config might be needed for paths, etc.
import config

# Import the normalization and utility functions
from utils.path_utils import normalize_path, human_readable_size
from utils.minhash_utils import compute_minhash_signature, estimate_jaccard_fast

# Use TYPE_CHECKING for type hints to avoid circular imports
if TYPE_CHECKING:
    from gui.main_window import ImageGallery

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
        self.image_list.setWordWrap(True)
        self.image_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.image_list.customContextMenuRequested.connect(self._show_dupe_context_menu)
        # Auto-load more when scrolling to bottom
        self.image_list.verticalScrollBar().valueChanged.connect(self._on_image_list_scroll)
        self.right_layout.addWidget(self.image_list)

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

        # Duplicate Detection Section - Row 1: Thresholds
        dupe_row1 = QHBoxLayout()
        self._threshold_offset = 15  # Remember user's preferred offset (default 15%)
        
        # Catch Threshold (LSH)
        self.catch_threshold_label = QLabel("Catch:")
        self.catch_threshold_spinbox = QSpinBox()
        self.catch_threshold_spinbox.setRange(50, 97)
        self.catch_threshold_spinbox.setValue(75)
        self.catch_threshold_spinbox.setSuffix("%")
        self.catch_threshold_spinbox.valueChanged.connect(self._on_catch_threshold_changed)
        
        # Display Threshold
        self.display_threshold_label = QLabel("Display:")
        self.display_threshold_label.setToolTip(
            "Only show pairs with similarity at or above this value.\n"
            "When linked, auto-set to Catch + offset."
        )
        self.display_threshold_spinbox = QSpinBox()
        self.display_threshold_spinbox.setRange(50, 100)
        self.display_threshold_spinbox.setValue(90)
        self.display_threshold_spinbox.setSuffix("%")
        self.display_threshold_spinbox.setToolTip(self.display_threshold_label.toolTip())
        self.display_threshold_spinbox.valueChanged.connect(self._on_display_threshold_changed)
        
        # Link Checkbox
        self.link_thresholds_checkbox = QCheckBox("Link")
        self.link_thresholds_checkbox.setChecked(True)
        self.link_thresholds_checkbox.setToolTip("When checked, Display = Catch + offset")
        self.link_thresholds_checkbox.stateChanged.connect(self._on_link_thresholds_changed)
        self._update_display_threshold_from_catch()  # Set initial linked value
        self._update_catch_tooltip()  # Set initial tooltip (after display spinbox exists)
        
        # Detect Button
        self.detect_dupes_button = QPushButton("Detect Dupes")
        self.detect_dupes_button.clicked.connect(self.detect_dupes_action)
        
        dupe_row1.addWidget(self.catch_threshold_label)
        dupe_row1.addWidget(self.catch_threshold_spinbox)
        dupe_row1.addSpacing(10)
        dupe_row1.addWidget(self.display_threshold_label)
        dupe_row1.addWidget(self.display_threshold_spinbox)
        dupe_row1.addWidget(self.link_thresholds_checkbox)
        dupe_row1.addStretch(1)
        dupe_row1.addWidget(self.detect_dupes_button)
        self.right_layout.addLayout(dupe_row1)

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
                if parent_dir:
                    normalized_dir = normalize_path(parent_dir)
                    if normalized_dir: # Ensure not empty after normalization
                        directories.add(normalized_dir)
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
            # Normalize the path from the dialog before displaying
            self.directory_text.setText(normalize_path(directory))

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

        normalized_directory = normalize_path(directory) # Use imported function
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

    # --- Threshold Linking Helpers ---
    def _on_catch_threshold_changed(self, value: int):
        """Called when catch threshold changes - updates display if linked, updates tooltip."""
        self._update_catch_tooltip()
        if self.link_thresholds_checkbox.isChecked():
            # Update display based on current offset
            display_val = max(50, min(value + self._threshold_offset, 100))
            self.display_threshold_spinbox.blockSignals(True)
            self.display_threshold_spinbox.setValue(display_val)
            self.display_threshold_spinbox.blockSignals(False)

    def _on_display_threshold_changed(self, value: int):
        """Called when display threshold changes - updates catch if linked, or remembers offset."""
        if self.link_thresholds_checkbox.isChecked():
            # Linked: update catch to maintain the offset
            new_catch = max(50, min(value - self._threshold_offset, 97))
            self.catch_threshold_spinbox.blockSignals(True)
            self.catch_threshold_spinbox.setValue(new_catch)
            self.catch_threshold_spinbox.blockSignals(False)
        else:
            # Not linked: remember the new offset for when user re-links
            catch_val = self.catch_threshold_spinbox.value()
            self._threshold_offset = value - catch_val
        
        self._update_catch_tooltip()

    def _on_link_thresholds_changed(self, state: int):
        """Called when link checkbox changes."""
        # Both spinboxes always remain enabled - linking just syncs them
        if state == Qt.CheckState.Checked.value:
            # When linking, ensure offset is current
            catch_val = self.catch_threshold_spinbox.value()
            display_val = self.display_threshold_spinbox.value()
            self._threshold_offset = display_val - catch_val

    def _update_display_threshold_from_catch(self):
        """Sets display threshold to catch + offset, capped at 50-100."""
        catch_val = self.catch_threshold_spinbox.value()
        display_val = max(50, min(catch_val + self._threshold_offset, 100))
        self.display_threshold_spinbox.setValue(display_val)

    def _update_catch_tooltip(self):
        """Updates tooltip with dynamic probability estimates based on catch and display thresholds."""
        catch = self.catch_threshold_spinbox.value()
        display = self.display_threshold_spinbox.value()
        
        # Estimate detection probabilities using a more accurate LSH model
        def estimate_prob(similarity: int, threshold: int) -> float:
            """Probability estimate - returns percentage with decimals."""
            diff = similarity - threshold
            if diff >= 25: return 99.99
            elif diff >= 20: return 99.9
            elif diff >= 15: return 99.0
            elif diff >= 10: return 95.0
            elif diff >= 5: return 85.0
            elif diff >= 0: return 50.0
            elif diff >= -5: return 25.0
            elif diff >= -10: return 10.0
            else: return 2.0
        
        # Show probabilities relevant to the display threshold
        # Start from display and go down in steps
        p_display = estimate_prob(display, catch)
        p_mid1 = estimate_prob(display - 5, catch) if display > 55 else None
        p_mid2 = estimate_prob(display - 10, catch) if display > 60 else None
        p_catch = estimate_prob(catch, catch)
        
        lines = [
            f"How aggressively to search for duplicates.",
            f"Lower = catches more edge cases, slightly slower.",
            f"",
            f"Detection rates at Catch={catch}%:"
        ]
        
        lines.append(f"• {display}% similar (Display): ~{p_display:.1f}% caught")
        if p_mid1 is not None and display - 5 > catch:
            lines.append(f"• {display-5}% similar: ~{p_mid1:.1f}% caught")
        if p_mid2 is not None and display - 10 > catch:
            lines.append(f"• {display-10}% similar: ~{p_mid2:.1f}% caught")
        lines.append(f"• {catch}% (Catch threshold): ~{p_catch:.1f}% caught")
        
        tooltip = "\n".join(lines)
        self.catch_threshold_label.setToolTip(tooltip)
        self.catch_threshold_spinbox.setToolTip(tooltip)

    # --- Duplicate Detection ---
    def detect_dupes_action(self):
        """Starts the duplicate detection process."""
        print("detect_dupes_action: Starting duplicate detection")
        self.image_list.clear()
        self.image_list.addItem("Starting duplicate detection...")

        self.set_ui_enabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        # Get both thresholds
        catch_threshold = self.catch_threshold_spinbox.value() / 100.0
        display_threshold = self.display_threshold_spinbox.value() / 100.0
        print(f"detect_dupes_action: Catch threshold: {catch_threshold}, Display threshold: {display_threshold}")

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

        worker = Worker(self.compare_image_tags, 
                       image_paths=image_paths_in_scope, 
                       catch_threshold=catch_threshold,
                       display_threshold=display_threshold,
                       status_callback=self.updateStatusText.emit)
        worker.signals.finished.connect(self.on_detection_finished)
        worker.signals.error.connect(self.on_detection_error)

        self.threadpool.start(worker)
        print("detect_dupes_action: Comparison worker started.")
        self.image_list.clear()
        self.image_list.addItem("Duplicate detection running in background...")


    def compare_image_tags(self, image_paths: List[str], catch_threshold: float, display_threshold: float, status_callback: Optional[Callable[[str], None]] = None) -> List[Tuple[str, str, float]]:
        """
        Compares images based on Jaccard similarity using MinHash + LSH.
        LSH (Locality Sensitive Hashing) avoids O(n²) comparisons by only comparing
        items that hash to the same bucket.
        
        Args:
            catch_threshold: Threshold for LSH bucketing (lower catches more)
            display_threshold: Threshold for filtering results (only show >= this)
        """
        from datasketch import MinHash, MinHashLSH
        from utils.minhash_utils import NUM_PERMUTATIONS
        import struct
        
        print(f"compare_image_tags: MinHash+LSH for {len(image_paths)} images, catch={catch_threshold}, display={display_threshold}")
        if len(image_paths) < 2: return []

        # Step 1: Fetch existing MinHash signatures
        if status_callback: status_callback(f"Fetching MinHash signatures for {len(image_paths)} images...\n")
        signatures = self.db.get_minhash_signatures_for_paths(image_paths)
        
        # Step 2: Identify images missing signatures and compute them
        paths_needing_signatures = [p for p in image_paths if signatures.get(p) is None]
        if paths_needing_signatures:
            if status_callback: status_callback(f"Computing signatures for {len(paths_needing_signatures)} images...\n")
            print(f"compare_image_tags: Computing signatures for {len(paths_needing_signatures)} images...")
            
            for idx, path in enumerate(paths_needing_signatures):
                tags = self.db.get_tags_for_path(path)
                if tags:
                    sig = compute_minhash_signature(tags)
                    signatures[path] = sig
                    self.db.update_minhash_signature(path, sig)
                
                if status_callback and (idx + 1) % 100 == 0:
                    status_callback(f"Computing signatures: {idx + 1}/{len(paths_needing_signatures)}...\n")
        
        # Step 3: Build LSH index for fast candidate retrieval
        valid_paths = [p for p in image_paths if signatures.get(p)]
        n = len(valid_paths)
        
        if n < 2:
            if status_callback: status_callback("Not enough images with valid signatures to compare.\n")
            return []
        
        if status_callback: status_callback(f"Building LSH index for {n} images...\n")
        print(f"compare_image_tags: Building LSH index for {n} images...")
        
        # Create LSH index - cap threshold at 0.97 because very high thresholds cause LSH errors
        # (LSH needs at least 2 bands, which isn't possible at extremely high thresholds)
        lsh_threshold = min(catch_threshold, 0.97)
        if lsh_threshold < catch_threshold:
            if status_callback: status_callback(f"Note: Using LSH threshold 0.97 (will filter to {catch_threshold:.0%} after)\n")
        
        try:
            lsh = MinHashLSH(threshold=lsh_threshold, num_perm=NUM_PERMUTATIONS)
        except ValueError as e:
            # Fallback: if LSH still fails, use 0.90 threshold for more candidates
            print(f"LSH init failed: {e}, falling back to 0.90 threshold")
            if status_callback: status_callback(f"LSH threshold adjusted for compatibility...\n")
            lsh = MinHashLSH(threshold=0.90, num_perm=NUM_PERMUTATIONS)
        
        # Convert byte signatures back to MinHash objects and insert into LSH
        import numpy as np
        path_to_minhash: Dict[str, MinHash] = {}
        for path in valid_paths:
            sig_bytes = signatures[path]
            if sig_bytes and len(sig_bytes) == NUM_PERMUTATIONS * 4:
                mh = MinHash(num_perm=NUM_PERMUTATIONS)
                # Must use numpy array, not list - datasketch requires it
                mh.hashvalues = np.array(struct.unpack(f'{NUM_PERMUTATIONS}I', sig_bytes), dtype=np.uint64)
                path_to_minhash[path] = mh
                lsh.insert(path, mh)
        
        # Step 4: Query LSH for candidate pairs (MUCH faster than O(n²))
        if status_callback: status_callback(f"Finding similar pairs using LSH...\n")
        print(f"compare_image_tags: Querying LSH for candidates...")
        
        seen_pairs = set()  # To avoid duplicate pairs
        comparison_results: List[Tuple[str, str, float]] = []
        candidates_checked = 0
        
        for idx, path1 in enumerate(valid_paths):
            mh1 = path_to_minhash.get(path1)
            if not mh1:
                continue
            
            # Query LSH for similar items (returns only likely matches!)
            candidates = lsh.query(mh1)
            
            for path2 in candidates:
                if path1 == path2:
                    continue
                
                # Create normalized pair key to avoid duplicates
                pair_key = tuple(sorted([path1, path2]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                
                mh2 = path_to_minhash.get(path2)
                if not mh2:
                    continue
                
                # Compute exact MinHash Jaccard for candidates
                similarity = mh1.jaccard(mh2)
                candidates_checked += 1
                
                if similarity >= display_threshold:
                    comparison_results.append((path1, path2, similarity))
            
            # Progress update every 500 images
            if status_callback and (idx + 1) % 500 == 0:
                status_callback(f"Processed {idx + 1}/{n} images, found {len(comparison_results)} pairs...\n")
        
        if status_callback: 
            status_callback(f"LSH checked {candidates_checked:,} candidate pairs (vs {n*(n-1)//2:,} brute force)\n")
        
        comparison_results.sort(key=lambda x: x[2], reverse=True)
        print(f"compare_image_tags: Found {len(comparison_results)} pairs above threshold (checked {candidates_checked:,} candidates)")
        if status_callback: status_callback(f"Complete! Found {len(comparison_results)} duplicate pairs.\n")
        return comparison_results


    def on_detection_finished(self, comparison_results: List[Tuple[str, str, float]]):
        """Handles the results from the duplicate detection worker with pagination."""
        print(f"on_detection_finished: Received {len(comparison_results)} pairs.")
        self.set_ui_enabled(True)
        QApplication.restoreOverrideCursor()
        self.image_list.clear()

        if not comparison_results:
            self.image_list.addItem("No similar image pairs found above the threshold.\n")
            self.updateStatusText.emit("Duplicate detection complete: No pairs found.\n")
            return

        # Store results for pagination
        self._dupe_results = comparison_results
        self._dupe_page = 0
        self._dupe_page_size = 50  # Load 50 pairs at a time (after initial load)
        self._initial_load_size = 100  # First load is larger for initial scrolling
        
        self.updateStatusText.emit(f"Duplicate detection complete: Found {len(comparison_results)} pairs.\n")
        self._load_dupe_page(initial=True)

    def _load_dupe_page(self, initial: bool = False):
        """Loads the next page of duplicate pairs into the list (auto-triggered by scroll)."""
        if not hasattr(self, '_dupe_results') or not self._dupe_results:
            return
        
        # Use larger page size for initial load
        page_size = self._initial_load_size if initial else self._dupe_page_size
        
        # Calculate which items to load
        start_idx = self._dupe_page * self._dupe_page_size if not initial else 0
        end_idx = min(start_idx + page_size, len(self._dupe_results))
        
        if start_idx >= len(self._dupe_results):
            return  # No more items to load
        
        # Load this page of items
        for pair_data in self._dupe_results[start_idx:end_idx]:
            self.add_dupe_pair_to_gui(pair_data)
        
        # Update page counter based on how many items were loaded
        if initial:
            self._dupe_page = (end_idx + self._dupe_page_size - 1) // self._dupe_page_size
        else:
            self._dupe_page += 1
        
        loaded_count = end_idx
        total_count = len(self._dupe_results)
        
        self.updateStatusText.emit(f"Showing {loaded_count}/{total_count} duplicate pairs.\n")

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
        # Threshold spinboxes - both always enabled
        self.catch_threshold_spinbox.setEnabled(enabled)
        self.display_threshold_spinbox.setEnabled(enabled)
        self.link_thresholds_checkbox.setEnabled(enabled)

    # --- Context Menu for Dupe Pairs ---
    def _show_dupe_context_menu(self, pos):
        """Shows context menu for dupe pairs with copy/open/delete actions."""
        item = self.image_list.itemAt(pos)
        if not item:
            return
        
        data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or "path1" not in data:
            return  # Not a dupe pair item
        
        path1 = data["path1"]
        path2 = data["path2"]
        
        menu = QMenu(self)
        
        # Copy actions
        copy_menu = menu.addMenu("Copy Path")
        copy_path1_action = copy_menu.addAction(f"Copy: {os.path.basename(path1)}")
        copy_path2_action = copy_menu.addAction(f"Copy: {os.path.basename(path2)}")
        copy_both_action = copy_menu.addAction("Copy Both Paths")
        
        menu.addSeparator()
        
        # Open folder actions
        open_folder1_action = menu.addAction(f"Open Folder: {os.path.basename(path1)}")
        open_folder2_action = menu.addAction(f"Open Folder: {os.path.basename(path2)}")
        
        menu.addSeparator()
        
        # Preview actions
        preview_menu = menu.addMenu("Open in Preview")
        preview_path1_action = preview_menu.addAction(f"Preview: {os.path.basename(path1)}")
        preview_path2_action = preview_menu.addAction(f"Preview: {os.path.basename(path2)}")
        
        menu.addSeparator()
        
        # Delete actions (with warning icons)
        delete_menu = menu.addMenu("Delete (Recycle Bin)")
        delete_path1_action = delete_menu.addAction(f"Delete: {os.path.basename(path1)}")
        delete_path2_action = delete_menu.addAction(f"Delete: {os.path.basename(path2)}")
        
        # Execute menu and handle actions
        action = menu.exec(self.image_list.mapToGlobal(pos))
        
        if action == copy_path1_action:
            QApplication.clipboard().setText(path1)
        elif action == copy_path2_action:
            QApplication.clipboard().setText(path2)
        elif action == copy_both_action:
            QApplication.clipboard().setText(f"{path1}\n{path2}")
        elif action == open_folder1_action:
            self._open_containing_folder(path1)
        elif action == open_folder2_action:
            self._open_containing_folder(path2)
        elif action == delete_path1_action:
            self._delete_to_recycle_bin(path1, item)
        elif action == delete_path2_action:
            self._delete_to_recycle_bin(path2, item)
        elif action == preview_path1_action:
            self._open_in_preview(path1)
        elif action == preview_path2_action:
            self._open_in_preview(path2)

    def _on_image_list_scroll(self, value: int):
        """Auto-loads more results when scrolling near the bottom."""
        scrollbar = self.image_list.verticalScrollBar()
        if scrollbar.value() >= scrollbar.maximum() - 50:  # Near bottom
            self._load_dupe_page()

    def _open_in_preview(self, path: str):
        """Opens an image in the main window preview panel."""
        if hasattr(self.main_window, 'display_image_in_preview'):
            self.main_window.display_image_in_preview(path)

    def _open_containing_folder(self, path: str):
        """Opens the folder containing the given file in the system file explorer."""
        folder = os.path.dirname(path)
        if os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            QMessageBox.warning(self, "Folder Not Found", f"Could not find folder:\n{folder}")

    def _delete_to_recycle_bin(self, path: str, list_item: QListWidgetItem):
        """Moves the file to recycle bin after confirmation."""
        # Normalize path to fix Windows extended path issues
        path = os.path.normpath(path)
        
        if not os.path.isfile(path):
            QMessageBox.warning(self, "File Not Found", f"File no longer exists:\n{path}")
            return
        
        filename = os.path.basename(path)
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Move this file to the Recycle Bin?\n\n{filename}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                # Use send2trash for cross-platform recycle bin support
                import send2trash
                send2trash.send2trash(path)
                QMessageBox.information(self, "Deleted", f"Moved to Recycle Bin:\n{filename}")
                # Remove the item from the list
                row = self.image_list.row(list_item)
                self.image_list.takeItem(row)
            except ImportError:
                # Fallback: just delete permanently with extra confirmation
                reply2 = QMessageBox.warning(
                    self, "Permanent Delete",
                    f"send2trash not installed. Permanently delete?\n\n{filename}\n\nThis cannot be undone!",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply2 == QMessageBox.StandardButton.Yes:
                    os.remove(path)
                    QMessageBox.information(self, "Deleted", f"Permanently deleted:\n{filename}")
                    row = self.image_list.row(list_item)
                    self.image_list.takeItem(row)
            except Exception as e:
                QMessageBox.critical(self, "Delete Error", f"Error deleting file:\n{e}")