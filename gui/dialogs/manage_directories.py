import os
import sys
import sqlite3
import datetime
import traceback
import math
from typing import TYPE_CHECKING, Set, List, Tuple, Optional, Dict, Callable
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter, QWidget, QListWidget,
    QPushButton, QLineEdit, QCheckBox, QSpinBox, QFileDialog, QMessageBox,
    QListWidgetItem, QAbstractItemView, QLabel, QApplication, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, QThreadPool, QSize, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap, QImage
from PIL import Image, UnidentifiedImageError

# Local imports (within the gui package)
from ..widgets.directory_list_item import DirectoryListItem

# Imports from other parts of the application package
from ...database.db_manager import Database
from ...utils.workers import Worker
# Assuming config might be needed for paths, etc.
from ... import config

# Use TYPE_CHECKING for type hints to avoid circular imports
if TYPE_CHECKING:
    from ..main_window import ImageGallery # The main application window

# Placeholder for utility function - move later
def human_readable_size(size_bytes: int) -> str:
    """Converts size in bytes to human-readable string."""
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"


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

        # Timer for duplicate detection progress updates
        self.progress_timer = QTimer(self)
        self.progress_timer.timeout.connect(self.update_progress_info)

        # Set initial dialog size based on screen
        try:
            screen = QApplication.primaryScreen()
            if screen:
                screen_size = screen.availableGeometry() # Use available geometry
                self.resize(int(screen_size.width() * 0.6), int(screen_size.height() * 0.7))
            else:
                 self.resize(1000, 700) # Default size if screen info fails
        except Exception as e:
             print(f"Error getting screen size: {e}")
             self.resize(1000, 700) # Default size

    def setup_ui(self):
        """Sets up the UI elements of the dialog."""
        self.layout = QHBoxLayout(self)

        # Splitter for left (directories) and right (images/duplicates) panels
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.layout.addWidget(self.splitter)

        # --- Left Panel (Directories) ---
        self.left_panel = QWidget()
        self.left_layout = QVBoxLayout(self.left_panel)

        # Directory List
        self.directory_list = QListWidget()
        self.directory_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.directory_list.itemSelectionChanged.connect(self.show_selected_images_in_right_panel) # Update right panel on selection
        self.left_layout.addWidget(QLabel("Monitored Directories:"))
        self.left_layout.addWidget(self.directory_list)

        # Directory Selection Buttons
        dir_buttons_layout = QHBoxLayout()
        select_all_dir_button = QPushButton("Select All Listed")
        select_all_dir_button.clicked.connect(self.directory_list.selectAll)
        unselect_all_dir_button = QPushButton("Unselect All Listed")
        unselect_all_dir_button.clicked.connect(self.directory_list.clearSelection)
        dir_buttons_layout.addWidget(select_all_dir_button)
        dir_buttons_layout.addWidget(unselect_all_dir_button)
        self.left_layout.addLayout(dir_buttons_layout)

        # Add Directory Section
        add_dir_group = QWidget() # Group related widgets
        add_dir_layout = QVBoxLayout(add_dir_group)
        add_dir_layout.setContentsMargins(0,5,0,0)
        add_dir_layout.addWidget(QLabel("Add New Directory:"))
        browse_layout = QHBoxLayout()
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.select_directory_to_add)
        self.directory_text = QLineEdit()
        self.directory_text.setPlaceholderText("Select directory to add...")
        self.directory_text.setReadOnly(True) # Path filled by browse
        browse_layout.addWidget(self.directory_text)
        browse_layout.addWidget(self.browse_button)
        add_dir_layout.addLayout(browse_layout)
        self.add_button = QPushButton("Add and Process Directory")
        self.add_button.clicked.connect(self.add_and_process_directory)
        add_dir_layout.addWidget(self.add_button)
        self.left_layout.addWidget(add_dir_group)

        # Action Buttons for selected directories
        action_buttons_layout = QHBoxLayout()
        self.process_button = QPushButton("Re-Process Selected")
        self.process_button.setToolTip("Scan selected directories for new/modified images.")
        self.process_button.clicked.connect(self.process_selected_directories_action)
        self.delete_button = QPushButton("Remove Selected")
        self.delete_button.setToolTip("Remove selected directories and all associated images from the database.")
        self.delete_button.clicked.connect(self.delete_selected_directories_action)
        action_buttons_layout.addWidget(self.process_button)
        action_buttons_layout.addWidget(self.delete_button)
        self.left_layout.addLayout(action_buttons_layout)

        self.splitter.addWidget(self.left_panel)

        # --- Right Panel (Images / Duplicates) ---
        self.right_panel = QWidget()
        self.right_layout = QVBoxLayout(self.right_panel)

        # Image/Duplicate List
        self.image_list = QListWidget()
        self.image_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # self.image_list.setWordWrap(True) # Enable word wrap for long info
        self.right_layout.addWidget(QLabel("Images in Selected / Duplicate Pairs:"))
        self.right_layout.addWidget(self.image_list)

        # Image Selection Buttons
        img_buttons_layout = QHBoxLayout()
        select_all_img_button = QPushButton("Select All Images")
        select_all_img_button.clicked.connect(self.image_list.selectAll)
        unselect_all_img_button = QPushButton("Unselect All Images")
        unselect_all_img_button.clicked.connect(self.image_list.clearSelection)
        img_buttons_layout.addWidget(select_all_img_button)
        img_buttons_layout.addWidget(unselect_all_img_button)
        self.right_layout.addLayout(img_buttons_layout)

        # Reprocess Section (for selected images)
        reprocess_group = QWidget()
        reprocess_layout = QVBoxLayout(reprocess_group)
        reprocess_layout.setContentsMargins(0,5,0,0)
        reprocess_layout.addWidget(QLabel("Reprocess Selected Images:"))
        self.properties_layout = QHBoxLayout()
        self.reprocess_tags_checkbox = QCheckBox("Tags")
        self.reprocess_thumbnail_checkbox = QCheckBox("Thumbnail")
        self.reprocess_metadata_checkbox = QCheckBox("Metadata (Size/Date/Res)") # Combined metadata
        self.properties_layout.addWidget(self.reprocess_tags_checkbox)
        self.properties_layout.addWidget(self.reprocess_thumbnail_checkbox)
        self.properties_layout.addWidget(self.reprocess_metadata_checkbox)
        reprocess_layout.addLayout(self.properties_layout)
        self.reprocess_button = QPushButton("Reprocess Selected Images")
        self.reprocess_button.clicked.connect(self.reprocess_selected_images_action)
        reprocess_layout.addWidget(self.reprocess_button)
        self.right_layout.addWidget(reprocess_group)

        # Duplicate Detection Section
        dupe_group = QWidget()
        dupe_layout = QVBoxLayout(dupe_group)
        dupe_layout.setContentsMargins(0,5,0,0)
        dupe_layout.addWidget(QLabel("Duplicate Detection (based on tags):"))
        self.dupe_detection_layout = QHBoxLayout()
        self.similarity_threshold_label = QLabel("Min Similarity (%):")
        self.similarity_threshold_spinbox = QSpinBox()
        self.similarity_threshold_spinbox.setRange(50, 100) # More realistic range
        self.similarity_threshold_spinbox.setValue(95)
        self.detect_dupes_button = QPushButton("Detect Duplicates in Selected Dirs")
        self.detect_dupes_button.clicked.connect(self.detect_dupes_action)
        self.dupe_detection_layout.addWidget(self.similarity_threshold_label)
        self.dupe_detection_layout.addWidget(self.similarity_threshold_spinbox)
        self.dupe_detection_layout.addWidget(self.detect_dupes_button)
        dupe_layout.addLayout(self.dupe_detection_layout)
        self.right_layout.addWidget(dupe_group)


        self.splitter.addWidget(self.right_panel)

        # Set initial splitter sizes
        self.splitter.setSizes([int(self.width() * 0.4), int(self.width() * 0.6)]) # Adjust ratio if needed

    def load_directories(self):
        """Load unique directory paths from the database and populate the list."""
        print("Loading directories from database...")
        try:
            with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                # Optimization: Get distinct directory parts directly
                cursor.execute("SELECT DISTINCT path FROM images")
                paths = [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"Error loading directory paths from DB: {e}")
            paths = []

        # Extract unique parent directories
        directories = set()
        for path_str in paths:
            try:
                # Assuming stored paths are normalized
                parent_dir = os.path.dirname(path_str)
                if parent_dir: # Avoid adding empty string if path is just a filename (shouldn't happen)
                    directories.add(parent_dir)
            except Exception as e:
                print(f"Error parsing directory from path '{path_str}': {e}")


        self.directory_list.clear()
        # Keep track of widgets to connect signals later if needed
        self.dir_list_widgets: List[DirectoryListItem] = []

        for directory in sorted(list(directories)):
            # Path should already be normalized from DB or os.dirname
            is_active = directory in self.active_directories # Check against initial/current active set
            item = QListWidgetItem()
            widget = DirectoryListItem(directory, is_checked=is_active) # Pass initial state
            widget.stateChanged.connect(self._handle_directory_state_change) # Connect signal
            item.setSizeHint(widget.sizeHint()) # Help list sizing
            self.directory_list.addItem(item)
            self.directory_list.setItemWidget(item, widget)
            self.dir_list_widgets.append(widget)

        print(f"Loaded {len(directories)} unique directories.")
        # Emit initial active set (or rely on checkbox signals to build it)
        # self.activeDirectoriesChanged.emit(self.active_directories) # Emit initial set

    def _handle_directory_state_change(self, directory: str, is_active: bool):
        """Slot to update the internal active_directories set and emit signal."""
        if is_active:
            self.active_directories.add(directory)
        else:
            self.active_directories.discard(directory)
        print(f"Active directories updated: {self.active_directories}")
        # Emit the changed set to the main window
        self.activeDirectoriesChanged.emit(self.active_directories)
        # Refresh the right panel if a directory's state changed
        self.show_selected_images_in_right_panel()


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
        self.image_paths_in_list.clear() # Clear previous list

        if not selected_dirs:
            self.image_list.addItem("Select directories on the left to see images.")
            return

        print(f"Showing images for selected directories: {selected_dirs}")
        try:
            with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                all_rows = []
                for directory in selected_dirs:
                    # Assumes directory path is normalized
                    if not directory.endswith('/'): directory += '/'
                    cursor.execute("""
                        SELECT id, path, file_size, modification_time, resolution
                        FROM images
                        WHERE path LIKE ?
                        ORDER BY path ASC
                    """, (f"{directory}%",))
                    all_rows.extend(cursor.fetchall())

            if not all_rows:
                 self.image_list.addItem("No images found in the selected directories.")
                 return

            # Sort combined results if needed (already sorted by path within each dir)
            # all_rows.sort(key=lambda row: row[1]) # Sort by path

            for row in all_rows:
                image_id, path, file_size, mod_time, resolution = row
                size_str = human_readable_size(file_size if file_size else 0)
                try:
                    date_str = datetime.datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M') if mod_time else "N/A"
                except ValueError:
                     date_str = "Invalid Date" # Handle potential timestamp errors

                # Create list item text (consider using a custom widget later for better formatting)
                item_text = f"ID: {image_id}\nPath: {path}\nInfo: {size_str}, {resolution or 'N/A'}, {date_str}"
                list_item = QListWidgetItem(item_text)
                list_item.setData(Qt.ItemDataRole.UserRole, image_id) # Store ID for later retrieval
                self.image_list.addItem(list_item)
                self.image_paths_in_list.append((image_id, path)) # Store for reprocessing

        except sqlite3.Error as e:
            print(f"Error fetching images for right panel: {e}")
            self.image_list.addItem(f"Error loading images: {e}")


    def select_directory_to_add(self):
        """Opens a dialog to select a directory to add."""
        # Use the last known directory or home as starting point
        start_dir = os.path.dirname(self.directory_text.text()) if self.directory_text.text() else str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "Select Directory to Add", start_dir)
        if directory:
            print(f"Directory selected to add: {directory}")
            self.directory_text.setText(self.db.normalize_path(directory)) # Show normalized path

    def add_and_process_directory(self):
        """Adds the selected directory to the list and triggers processing."""
        directory = self.directory_text.text().strip()
        if not directory:
            QMessageBox.warning(self, "No Directory", "Please select a directory using 'Browse...' first.")
            return

        if not os.path.isdir(directory):
            QMessageBox.warning(self, "Invalid Directory", f"The selected path is not a valid directory:\n{directory}")
            return

        # Check if already in the list
        normalized_directory = self.db.normalize_path(directory)
        for i in range(self.directory_list.count()):
            widget = self.directory_list.itemWidget(self.directory_list.item(i))
            if widget and widget.getDirectory() == normalized_directory:
                QMessageBox.information(self, "Already Added", "This directory is already being monitored.")
                return

        # Add to the list visually (will be fully loaded on next load_directories)
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

        # Emit signal to main window to process this new directory
        print(f"Requesting processing for newly added directory: {normalized_directory}")
        self.processDirectoriesRequested.emit([normalized_directory])
        QMessageBox.information(self, "Directory Added", f"Directory added and processing started:\n{normalized_directory}")


    def process_selected_directories_action(self):
        """Emits signal to re-process selected directories."""
        selected_dirs = self.get_selected_directory_paths()
        if not selected_dirs:
            QMessageBox.warning(self, "No Selection", "Please select directories from the list to re-process.")
            return

        print(f"Requesting re-processing for selected directories: {selected_dirs}")
        self.processDirectoriesRequested.emit(selected_dirs)
        QMessageBox.information(self, "Processing Started", f"Re-processing started for {len(selected_dirs)} selected directories.")


    def delete_selected_directories_action(self):
        """Confirms and emits signal to delete selected directories."""
        selected_dirs = self.get_selected_directory_paths()
        if not selected_dirs:
            QMessageBox.warning(self, "No Selection", "Please select directories from the list to remove.")
            return

        reply = QMessageBox.question(
            self, "Confirm Removal",
            f"Are you sure you want to remove the following directories and all their images from the database?\n\n" + "\n".join(selected_dirs) +
            f"\n\n(This only removes database entries, not the actual files on disk.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            print(f"Requesting deletion for selected directories: {selected_dirs}")
            # Emit signal to main window
            self.deleteDirectoriesRequested.emit(selected_dirs)
            # Remove from the list view immediately for responsiveness
            items_to_remove = []
            widgets_to_remove = []
            for i in range(self.directory_list.count()):
                 item = self.directory_list.item(i)
                 widget = self.directory_list.itemWidget(item)
                 if widget and widget.getDirectory() in selected_dirs:
                      items_to_remove.append(item)
                      widgets_to_remove.append(widget)
                      self.active_directories.discard(widget.getDirectory()) # Update local active set

            for item in items_to_remove:
                 self.directory_list.takeItem(self.directory_list.row(item))
            for widget in widgets_to_remove:
                 self.dir_list_widgets.remove(widget)
                 widget.deleteLater() # Clean up widget

            # Emit updated active directories
            self.activeDirectoriesChanged.emit(self.active_directories)

            QMessageBox.information(self, "Removal Requested", f"Removal requested for {len(selected_dirs)} directories.")


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

        properties_to_reprocess = {
            "tags": self.reprocess_tags_checkbox.isChecked(),
            "thumbnail": self.reprocess_thumbnail_checkbox.isChecked(),
            "metadata": self.reprocess_metadata_checkbox.isChecked(),
        }

        if not any(properties_to_reprocess.values()):
            QMessageBox.warning(self, "No Properties Selected", "Please check at least one property (Tags, Thumbnail, Metadata) to reprocess.")
            return

        print(f"Requesting reprocessing for {len(image_ids_to_reprocess)} images. Properties: {properties_to_reprocess}")
        self.reprocessImagesRequested.emit(image_ids_to_reprocess, properties_to_reprocess)
        QMessageBox.information(self, "Reprocessing Started", f"Reprocessing started for {len(image_ids_to_reprocess)} selected images.")


    # --- Duplicate Detection ---

    def detect_dupes_action(self):
        """Starts the duplicate detection process."""
        print("detect_dupes_action: Starting duplicate detection")
        self.image_list.clear() # Clear the right panel for results
        self.image_list.addItem("Starting duplicate detection...")

        # Disable UI elements during detection
        self.set_ui_enabled(False)

        similarity_threshold = self.similarity_threshold_spinbox.value() / 100.0
        print(f"detect_dupes_action: Similarity threshold: {similarity_threshold}")

        # Use currently active directories for duplicate search scope
        dirs_to_search = list(self.active_directories)
        if not dirs_to_search:
            QMessageBox.information(self, "No Active Directories", "Please ensure at least one directory is checked as active to search for duplicates.")
            self.set_ui_enabled(True)
            self.image_list.clear()
            return

        print(f"detect_dupes_action: Searching in active directories: {dirs_to_search}")

        # Get all image paths within the active directories
        image_paths_in_scope = []
        try:
            with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                for directory in dirs_to_search:
                    norm_dir = self.db.normalize_path(directory)
                    if not norm_dir.endswith('/'): norm_dir += '/'
                    cursor.execute("SELECT path FROM images WHERE path LIKE ?", (f"{norm_dir}%",))
                    image_paths_in_scope.extend([row[0] for row in cursor.fetchall()])
            print(f"detect_dupes_action: Found {len(image_paths_in_scope)} images in scope.")
        except sqlite3.Error as e:
             QMessageBox.critical(self, "Database Error", f"Error fetching images for duplicate check: {e}")
             self.set_ui_enabled(True)
             self.image_list.clear()
             return

        if len(image_paths_in_scope) < 2:
            QMessageBox.information(self, "Not Enough Images", "Need at least two images in the active directories to compare.")
            self.set_ui_enabled(True)
            self.image_list.clear()
            self.image_list.addItem("Need at least two images to compare.")
            return

        # Start the comparison worker
        worker = Worker(self.compare_image_tags, image_paths=image_paths_in_scope, similarity_threshold=similarity_threshold)
        worker.signals.finished.connect(self.on_detection_finished)
        worker.signals.error.connect(self.on_detection_error)
        # Connect progress signal if compare_image_tags provides it
        # worker.signals.progress.connect(self.update_progress_display)
        worker.signals.update_info_text.connect(self.updateStatusText.emit) # Pass status updates

        self.threadpool.start(worker)
        print("detect_dupes_action: Comparison worker started.")
        self.image_list.clear()
        self.image_list.addItem("Duplicate detection running in background...")


    def compare_image_tags(self, image_paths: List[str], similarity_threshold: float, status_callback: Optional[Callable[[str], None]] = None) -> List[Tuple[str, str, float]]:
        """
        Compares images based on Jaccard similarity of their tags.

        Args:
            image_paths: List of absolute, normalized image paths to compare.
            similarity_threshold: The minimum Jaccard index (0.0 to 1.0) to consider a pair similar.
            status_callback: Optional function to emit status updates.

        Returns:
            A list of tuples: (path1, path2, similarity_score) for pairs meeting the threshold.
        """
        print(f"compare_image_tags: Starting comparison for {len(image_paths)} images, threshold {similarity_threshold}")
        if len(image_paths) < 2: return []

        # 1. Fetch tags for all images efficiently
        image_tags_map: Dict[str, Set[str]] = {} # path -> set(tags)
        try:
            with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                # Use placeholders for a potentially large number of paths
                path_placeholders = ','.join('?' for _ in image_paths)
                cursor.execute(f"""
                    SELECT i.path, t.name
                    FROM images i
                    JOIN image_tags it ON i.id = it.image_id
                    JOIN tags t ON it.tag_id = t.id
                    WHERE i.path IN ({path_placeholders})
                """, image_paths)
                rows = cursor.fetchall()
                for path, tag_name in rows:
                    if path not in image_tags_map:
                        image_tags_map[path] = set()
                    image_tags_map[path].add(tag_name)
            print(f"compare_image_tags: Fetched tags for {len(image_tags_map)} images.")
        except sqlite3.Error as e:
            print(f"Database error fetching tags for comparison: {e}")
            if status_callback: status_callback(f"Error fetching tags: {e}")
            return []

        # 2. Perform pairwise comparison
        comparison_results: List[Tuple[str, str, float]] = []
        path_list = list(image_tags_map.keys()) # Use paths that actually have tags
        n = len(path_list)
        total_comparisons = n * (n - 1) // 2
        completed_comparisons = 0
        last_status_update_time = datetime.datetime.now().timestamp()

        print(f"compare_image_tags: Starting {total_comparisons} pairwise comparisons...")
        if status_callback: status_callback(f"Comparing {n} images ({total_comparisons} pairs)...")

        for i in range(n):
            path1 = path_list[i]
            tags1 = image_tags_map[path1]
            if not tags1: continue # Skip images with no tags

            for j in range(i + 1, n):
                path2 = path_list[j]
                tags2 = image_tags_map.get(path2) # Use .get in case path somehow missing
                if not tags2: continue # Skip images with no tags

                # Calculate Jaccard Similarity
                intersection = len(tags1.intersection(tags2))
                union = len(tags1.union(tags2))
                similarity = intersection / union if union > 0 else 0.0

                if similarity >= similarity_threshold:
                    comparison_results.append((path1, path2, similarity))

                completed_comparisons += 1

                # Update status periodically
                current_time = datetime.datetime.now().timestamp()
                if status_callback and (current_time - last_status_update_time >= 1.0 or completed_comparisons == total_comparisons):
                    progress_percentage = (completed_comparisons / total_comparisons) * 100 if total_comparisons > 0 else 100
                    status_callback(f"Comparing: {completed_comparisons}/{total_comparisons} pairs ({progress_percentage:.1f}%)")
                    last_status_update_time = current_time

        # Sort results by similarity descending
        comparison_results.sort(key=lambda x: x[2], reverse=True)
        print(f"compare_image_tags: Found {len(comparison_results)} pairs above threshold.")
        if status_callback: status_callback(f"Comparison complete. Found {len(comparison_results)} potential duplicate pairs.")

        return comparison_results


    def on_detection_finished(self, comparison_results: List[Tuple[str, str, float]]):
        """Handles the results from the duplicate detection worker."""
        print(f"on_detection_finished: Received {len(comparison_results)} pairs.")
        self.set_ui_enabled(True) # Re-enable UI
        self.image_list.clear() # Clear "Running..." message

        if not comparison_results:
            self.image_list.addItem("No similar image pairs found above the threshold.")
            self.updateStatusText.emit("Duplicate detection complete: No pairs found.")
            return

        self.updateStatusText.emit(f"Duplicate detection complete: Found {len(comparison_results)} pairs. Populating list...")

        # Populate the list with the found pairs
        for pair_data in comparison_results:
            self.add_dupe_pair_to_gui(pair_data)

        self.updateStatusText.emit(f"Duplicate detection complete: Displaying {len(comparison_results)} pairs.")


    def on_detection_error(self, error_info: tuple):
        """Handles errors reported by the duplicate detection worker."""
        exception, traceback_str = error_info
        print(f"Duplicate detection worker failed: {exception}", file=sys.stderr)
        self.set_ui_enabled(True) # Re-enable UI
        self.image_list.clear()
        self.image_list.addItem(f"Error during duplicate detection:\n{exception}")
        QMessageBox.critical(self, "Detection Error", f"An error occurred during duplicate detection:\n{exception}")
        self.updateStatusText.emit(f"Duplicate detection failed: {exception}")


    def add_dupe_pair_to_gui(self, pair_data: Tuple[str, str, float]):
        """Creates and adds a custom widget for a duplicate pair to the list."""
        try:
            path1, path2, similarity = pair_data
        except (ValueError, TypeError) as e:
            print(f"Error unpacking pair data in add_dupe_pair_to_gui: {e}, Data: {pair_data}")
            return

        # --- Create main widget for the list item ---
        pair_widget = QWidget()
        pair_layout = QHBoxLayout(pair_widget)
        pair_layout.setContentsMargins(5, 5, 5, 5) # Add some padding
        pair_layout.setSpacing(10)

        # --- Left Image Section ---
        left_section = QWidget()
        left_layout = QVBoxLayout(left_section)
        left_layout.setContentsMargins(0,0,0,0)
        left_thumb_label = QLabel()
        left_thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_info_label = QLabel()
        left_info_label.setWordWrap(True)
        left_layout.addWidget(left_thumb_label)
        left_layout.addWidget(left_info_label)

        # --- Right Image Section ---
        right_section = QWidget()
        right_layout = QVBoxLayout(right_section)
        right_layout.setContentsMargins(0,0,0,0)
        right_thumb_label = QLabel()
        right_thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_info_label = QLabel()
        right_info_label.setWordWrap(True)
        right_layout.addWidget(right_thumb_label)
        right_layout.addWidget(right_info_label)

        # --- Similarity Label ---
        similarity_label = QLabel(f"{similarity * 100:.1f}%")
        similarity_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # --- Add sections to main layout ---
        pair_layout.addWidget(left_section, 1) # Give stretch factor
        pair_layout.addWidget(similarity_label)
        pair_layout.addWidget(right_section, 1) # Give stretch factor

        # --- Populate with data ---
        thumb_height = 80 # Smaller thumbnails for pairs list
        self.populate_dupe_section(left_thumb_label, left_info_label, path1, thumb_height)
        self.populate_dupe_section(right_thumb_label, right_info_label, path2, thumb_height)

        # --- Add to QListWidget ---
        list_item = QListWidgetItem()
        list_item.setSizeHint(pair_widget.sizeHint()) # Set size hint
        # Store paths in the item data for potential actions later
        list_item.setData(Qt.ItemDataRole.UserRole, {"path1": path1, "path2": path2})
        self.image_list.addItem(list_item)
        self.image_list.setItemWidget(list_item, pair_widget)


    def populate_dupe_section(self, thumb_label: QLabel, info_label: QLabel, path: str, thumb_height: int):
        """Helper to load thumbnail and info for one image in a duplicate pair."""
        image_id = self.db.get_image_id_from_path(path)
        pixmap = self.get_cached_thumbnail(image_id) if image_id else None

        if pixmap:
            thumb_label.setPixmap(pixmap.scaledToHeight(thumb_height, Qt.TransformationMode.SmoothTransformation))
        else:
            thumb_label.setText("No Thumb") # Placeholder

        # Get image info
        info_text = f"{os.path.basename(path)}\n"
        try:
            if Path(path).is_file():
                 size = os.path.getsize(path)
                 mod_time = os.path.getmtime(path)
                 date_str = datetime.datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M')
                 with Image.open(path) as img:
                     res_str = f"{img.width}x{img.height}"
                 info_text += f"{human_readable_size(size)}, {res_str}, {date_str}"
            else:
                 info_text += "(File not found)"
        except Exception as e:
            print(f"Error getting info for {path}: {e}")
            info_text += "(Error getting info)"

        info_label.setText(info_text)


    def get_cached_thumbnail(self, image_id: Optional[str]) -> Optional[QPixmap]:
        """Safely retrieves a QPixmap thumbnail from the cache via the main window."""
        if image_id and hasattr(self.main_window, 'thumbnail_cache'):
            qimage = self.main_window.thumbnail_cache.get_thumbnail(image_id)
            if qimage and not qimage.isNull():
                return QPixmap.fromImage(qimage)
        return None

    def update_progress_info(self):
        """Placeholder for timer-based progress updates if needed."""
        # This was used differently before, now status updates come from worker
        pass

    def set_ui_enabled(self, enabled: bool):
        """Enables or disables relevant UI elements during long operations."""
        # Disable buttons that trigger actions
        self.add_button.setEnabled(enabled)
        self.process_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        self.reprocess_button.setEnabled(enabled)
        self.detect_dupes_button.setEnabled(enabled)
        # Disable list interactions? Maybe not necessary if buttons are disabled.
        # self.directory_list.setEnabled(enabled)
        # self.image_list.setEnabled(enabled)
        # Disable checkboxes?
        # self.reprocess_tags_checkbox.setEnabled(enabled)
        # ... etc ...

    # Override closeEvent or reject to ensure main window state is updated?
    # def reject(self):
    #     # Ensure latest active directories are emitted before closing
    #     self.activeDirectoriesChanged.emit(self.active_directories)
    #     super().reject()