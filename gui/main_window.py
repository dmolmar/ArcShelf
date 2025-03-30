import sys
import os
import math
import datetime
import sqlite3
import random
import traceback
import gc
import re
from pathlib import Path
from typing import List, Optional, Set, Tuple, Dict, Callable, Any
from queue import Queue
from collections import defaultdict

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QFileDialog, QVBoxLayout, QWidget,
    QLabel, QScrollArea, QHBoxLayout, QFrame, QDialog, QComboBox, # Removed QStyle
    QSlider, QSpinBox, QDoubleSpinBox, QSplitter, QTextEdit, QLineEdit, QListWidget, # Add QDoubleSpinBox
    QSizePolicy, QAbstractItemView, QMessageBox, QMenu,
    QCheckBox, QListWidgetItem # Add QListWidgetItem
)
from PyQt6.QtGui import (
    QPixmap, QDragEnterEvent, QDropEvent, QShortcut, # Removed QImageReader, QImage
    QIcon, QTextCursor, QAction, QKeyEvent
)
from PyQt6.QtCore import (
    Qt, QTimer, pyqtSignal, QObject, pyqtSlot, QRunnable, QThreadPool, QSize
)
from PIL import Image, UnidentifiedImageError

# --- Local Imports (within arc_explorer package) ---
import config # For base directory, paths
from database.db_manager import Database
from database.models import TagPrediction
from image_processing.thumbnail import ThumbnailCache
from image_processing.tagger import ImageTaggerModel
from search.query_parser import SearchQueryParser, ASTNode # Import base ASTNode
from search.query_evaluator import SearchQueryEvaluator
from utils.workers import Worker, ThumbnailLoader # Assuming ThumbnailLoaderSignals is not needed directly here
from gui.widgets.image_label import ImageLabel
from gui.widgets.drag_drop_area import DragDropArea
from gui.widgets.advanced_search import AdvancedSearchPanel # Import the actual panel
# Dialogs will be imported within methods where needed
# from .dialogs.manage_directories import ManageDirectoriesDialog
# from .dialogs.export_jpg import ExportAsJPGDialog

# --- Utility Function (Consider moving to utils module later) ---
def human_readable_size(size_bytes: Optional[int]) -> str:
    """Converts size in bytes to human-readable string."""
    if size_bytes is None or size_bytes < 0:
        return "N/A"
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    try:
        i = int(math.floor(math.log(size_bytes, 1024)))
        # Clamp index to the range of size_name
        i = max(0, min(i, len(size_name) - 1))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"
    except (ValueError, OverflowError):
        # Handle potential math errors for very large numbers
        return f"{size_bytes} B"


# --- Main Application Window ---
class ImageGallery(QMainWindow):
    # Define signals used for cross-thread communication or decoupling
    thumbnailLoaded = pyqtSignal(str, QPixmap) # image_id (str UUID), pixmap
    imageAnalysisSignal = pyqtSignal(str) # info_text
    imageInfoSignal = pyqtSignal(str, str) # info_text, img_path
    requestImageAnalysis = pyqtSignal(str) # Emit image path (str)
    updateInfoTextSignal = pyqtSignal(str) # General status text update
    suggestionVisibilityInfo = pyqtSignal(bool, int) # is_visible, count
    suggestionConfirmationFinished = pyqtSignal(bool) # True if confirmation happened, False otherwise

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Arc-Explorer")
        self.setGeometry(100, 100, 1600, 900) # Adjusted default size

        # Set window icon using config
        icon_path = config.ICON_PATH
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        else:
            print(f"Warning: Icon file not found at {icon_path}")

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)

        # --- Application State ---
        self.similarity_mode: bool = False
        self.last_selected_image_path: Optional[str] = None
        self.suppress_search_on_dropdown_update: bool = False
        self.active_directories: Set[str] = set()
        self.is_processing: bool = False
        self.processed_directories: List[str] = []
        self.total_images_to_process: int = 0
        self.processed_images_count: int = 0
        self.all_images: List[str] = []
        self.aspect_ratios: Dict[str, float] = {}
        self.current_page: int = 1
        self.total_pages: int = 1
        self.page_size: int = 50
        self.target_height: int = 250 # Default row height
        self.thumbnail_loaders: Dict[int, Tuple[ImageLabel, str]] = {}
        self._temp_pred_callback: Optional[Callable] = None # For drag-drop predictions
        self._suggestions_map: Dict[str, str] = {}
        self._ignore_cursor_change_on_focus = False

        # --- Slideshow State ---
        self.is_slideshow_active: bool = False
        self.slideshow_images: List[str] = []
        self.slideshow_current_index: int = -1
        self.slideshow_timer: QTimer = QTimer(self)
        self.slideshow_timer.setSingleShot(True)
        self.slideshow_button: Optional[QPushButton] = None
        self.slideshow_delay_spinbox: Optional[QDoubleSpinBox] = None # Changed type hint

        # --- Initialization ---
        self.threadpool = QThreadPool()
        print(f"Multithreading with maximum {self.threadpool.maxThreadCount()} threads")

        # Database and Thumbnail Cache
        self.thumbnail_cache = ThumbnailCache(config.CACHE_DIR)
        self.db = Database(config.DB_PATH, self.thumbnail_cache)

        self.aspect_ratios: Dict[str, float] = {} # Cache for calculated aspect ratios
        self.image_resolutions: Dict[str, Optional[str]] = {} # Cache for resolution strings from DB

        # Load initial active directories from DB
        self._load_initial_active_directories()

        # Model Initialization
        self.model = ImageTaggerModel(config.MODEL_PATH, config.TAGS_CSV_PATH)

        # --- UI Setup ---
        self.setup_ui()

        # --- Signal Connections ---
        self.setup_signals()

        # --- Initial Load ---
        self.perform_search() # Perform initial search/load
        self.update_suggestions() # Initial tag suggestions

        # Maximize the window on startup
        self.showMaximized()

    def _load_initial_active_directories(self):
        """Loads all unique directories from DB as initially active."""
        print("Loading initial active directories...")
        try:
            with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT path FROM images")
                paths = [row[0] for row in cursor.fetchall()]
            for path_str in paths:
                try:
                    parent_dir = os.path.dirname(path_str)
                    if parent_dir:
                        self.active_directories.add(parent_dir)
                except Exception as e:
                    print(f"Error parsing directory from path '{path_str}': {e}")
            print(f"Initial active directories: {self.active_directories}")
        except sqlite3.Error as e:
            print(f"Error loading initial directories from DB: {e}")

    def setup_ui(self):
        """Creates and arranges the UI widgets."""
        # --- Left Panel ---
        self.left_panel_container = QWidget()
        self.left_panel_layout = QVBoxLayout(self.left_panel_container)
        self.left_panel_layout.setContentsMargins(0, 0, 0, 0)

        # Vertical Splitter for Preview/Info
        self.left_v_splitter = QSplitter(Qt.Orientation.Vertical)
        self.drag_drop_area = DragDropArea(self)
        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)

        self.left_v_splitter.addWidget(self.drag_drop_area)
        self.left_v_splitter.addWidget(self.info_text)

        # Set initial relative sizes for the vertical splitter (e.g., 65% preview, 35% info)
        # Use integers for setSizes
        # We set this based on a reasonable guess, user can resize.
        # Avoid basing this on self.height() before the window is shown.
        self.left_v_splitter.setSizes([650, 350]) # Example initial distribution
        self.left_panel_layout.addWidget(self.left_v_splitter)
        
        # Suggestions List (Managed by Main Window)
        self.suggestions_list = QListWidget()
        self.suggestions_list.setFixedHeight(150) # Or adjust as needed
        self.suggestions_list.setAlternatingRowColors(True)
        self.suggestions_list.hide() # Initially hidden
        # Add it to the layout *between* the splitter and the search panel
        self.left_panel_layout.addWidget(self.suggestions_list)

        # Add other left panel widgets
        self.advanced_search_panel = AdvancedSearchPanel(self)
        self.left_panel_layout.addWidget(self.advanced_search_panel)
        self.manage_directories_button = QPushButton("Manage Directories & Duplicates...")
        self.left_panel_layout.addWidget(self.manage_directories_button)

        # --- Right Panel ---
        self.right_panel = QWidget()
        self.right_panel_layout = QVBoxLayout(self.right_panel)

        top_controls_layout = QHBoxLayout()
        slider_frame = QFrame()
        slider_layout = QHBoxLayout(slider_frame)
        slider_layout.setContentsMargins(0,0,0,0)
        # self.target_height = 250 # Now initialized in __init__
        self.slider_label = QLabel(f"Row Height: {self.target_height}px") # Initialize with value
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.min_row_height = 50
        self.max_row_height = 400 # Keep max reasonable, user can adjust
        self.slider.setRange(self.min_row_height, self.max_row_height)
        self.slider.setValue(self.target_height) # Set initial slider position
        slider_layout.addWidget(self.slider_label)
        slider_layout.addWidget(self.slider)
        top_controls_layout.addWidget(slider_frame)

        pagination_frame = QFrame()
        self.pagination_layout = QHBoxLayout(pagination_frame)
        self.pagination_layout.setContentsMargins(0,0,0,0)
        self.page_label = QLabel("Page:")
        self.page_number_edit = QSpinBox()
        self.page_number_edit.setMinimum(1)
        self.page_number_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.total_pages_label = QLabel("of 1")
        self.page_size_label = QLabel("Per Page:")
        self.page_size_edit = QSpinBox()
        self.page_size_edit.setRange(10, 500)
        self.page_size_edit.setValue(self.page_size)
        self.page_size_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.prev_button = QPushButton("<< Prev")
        self.next_button = QPushButton("Next >>")
        self.pagination_layout.addWidget(self.page_label)
        self.pagination_layout.addWidget(self.page_number_edit)
        self.pagination_layout.addWidget(self.total_pages_label)
        self.pagination_layout.addStretch(1)
        self.pagination_layout.addWidget(self.page_size_label)
        self.pagination_layout.addWidget(self.page_size_edit)
        self.pagination_layout.addWidget(self.prev_button)
        self.pagination_layout.addWidget(self.next_button)
        top_controls_layout.addWidget(pagination_frame)

        sorting_frame = QFrame()
        self.sorting_layout = QHBoxLayout(sorting_frame)
        self.sorting_layout.setContentsMargins(0,0,0,0)
        self.sorting_combo = QComboBox()
        self.sorting_combo.addItems(['Date', 'File Size', 'Resolution', 'Aspect Ratio', 'Random', 'Similarity'])
        self.sorting_combo.model().item(self.sorting_combo.count() - 1).setEnabled(False)
        self.sort_order_combo = QComboBox()
        self.sort_order_combo.addItems(['↓ Desc', '↑ Asc'])
        self.sort_order_combo.setCurrentText("↓ Desc")
        self.sorting_layout.addWidget(QLabel("Sort By:"))
        self.sorting_layout.addWidget(self.sorting_combo)
        self.sorting_layout.addWidget(self.sort_order_combo)
        top_controls_layout.addWidget(sorting_frame)

        self.total_images_label = QLabel("Total Images: 0")
        top_controls_layout.addWidget(self.total_images_label)

        # Slideshow controls are now added *after* the scroll area
        self.right_panel_layout.addLayout(top_controls_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_layout.setContentsMargins(5, 5, 5, 5)
        self.scroll_layout.setSpacing(10)
        self.scroll_area.setWidget(self.scroll_content)
        self.right_panel_layout.addWidget(self.scroll_area)

        # --- Slideshow Controls (Moved Here) ---
        self.slideshow_frame = QFrame() # Store as instance variable if needed elsewhere
        slideshow_layout = QHBoxLayout(self.slideshow_frame)
        slideshow_layout.setContentsMargins(5, 5, 5, 0) # Reduce bottom margin to 0
        self.slideshow_button = QPushButton("▶ Start Slideshow")
        self.slideshow_delay_label = QLabel("Delay (s):")
        self.slideshow_delay_spinbox = QDoubleSpinBox() # Changed to QDoubleSpinBox
        self.slideshow_delay_spinbox.setDecimals(1)     # Allow one decimal place
        self.slideshow_delay_spinbox.setRange(0.1, 120.0) # Range from 0.1s to 120s
        self.slideshow_delay_spinbox.setSingleStep(0.5) # Step by 0.5s
        self.slideshow_delay_spinbox.setValue(5.0)      # Default 5.0 seconds
        self.slideshow_delay_spinbox.setFixedWidth(75) # Slightly wider for decimals
        slideshow_layout.addStretch(1) # Push controls to the right
        slideshow_layout.addWidget(self.slideshow_button)
        slideshow_layout.addWidget(self.slideshow_delay_label)
        slideshow_layout.addWidget(self.slideshow_delay_spinbox)
        self.right_panel_layout.addWidget(self.slideshow_frame) # Add below scroll area
        # --- End Slideshow Controls ---
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.left_panel_container)
        self.splitter.addWidget(self.right_panel)
        initial_width = self.width() # Get initial window width
        initial_left_width = int(initial_width * 0.30) # 30% for left panel
        initial_right_width = initial_width - initial_left_width # Remaining for right
        self.splitter.setSizes([initial_left_width, initial_right_width])
        self.main_layout.addWidget(self.splitter)

        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.arrange_rows)

        self.suggestion_timer = QTimer(self)
        self.suggestion_timer.setSingleShot(True)
        self.suggestion_timer.timeout.connect(self.update_suggestions)

        self.splitter_resize_timer = QTimer(self)
        self.splitter_resize_timer.setSingleShot(True)
        self.splitter_resize_timer.timeout.connect(self.arrange_rows)
        self.splitter.splitterMoved.connect(lambda: self.splitter_resize_timer.start(50))

        self.central_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.central_widget.setFocus()
        QShortcut(Qt.Key.Key_Left, self, self.go_to_previous_page)
        QShortcut(Qt.Key.Key_Right, self, self.go_to_next_page)

    def setup_signals(self):
        """Connect signals to slots."""
        self.slider.valueChanged.connect(self.on_slider_moved)
        self.slider.valueChanged.connect(self.update_slider_label) # Connect to new slot
        self.page_number_edit.valueChanged.connect(self.on_page_changed)
        self.page_size_edit.valueChanged.connect(self.on_page_size_changed)
        self.prev_button.clicked.connect(self.go_to_previous_page)
        self.next_button.clicked.connect(self.go_to_next_page)
        self.sorting_combo.currentIndexChanged.connect(self.on_sorting_changed)
        self.sort_order_combo.currentIndexChanged.connect(self.on_sort_order_changed)
        self.splitter.splitterMoved.connect(self.on_splitter_moved)
        self.manage_directories_button.clicked.connect(self.open_manage_directories_dialog)

        self.advanced_search_panel.searchRequested.connect(self.perform_search)
        self.advanced_search_panel.inputChanged.connect(self.on_text_or_cursor_changed)

        self.advanced_search_panel.focusGained.connect(self._show_suggestions) # Connect NEW focus gained signal
        self.advanced_search_panel.focusLost.connect(self._hide_suggestions) # Connect focus lost signal (emitted by eventFilter now)

        self.advanced_search_panel.tagSegmentSelected.connect(self._handle_tag_segment_selected)
        self.advanced_search_panel.requestHideSuggestions.connect(self._hide_suggestions)

        # Add connection for confirmSuggestion
        self.advanced_search_panel.confirmSuggestion.connect(self._confirm_selected_suggestion)

        # Connect ASP request to IG handler
        self.advanced_search_panel.checkSuggestionVisibilityRequest.connect(
            self._handle_check_suggestion_visibility
        )
        # Connect ASP navigation request to IG handler
        self.advanced_search_panel.navigateSuggestions.connect(self.handleNavigateSuggestions)
        # Connect IG visibility info signal back to ASP slot
        self.suggestionVisibilityInfo.connect(
            self.advanced_search_panel.receiveSuggestionVisibilityInfo
        )
        # Connect IG confirmation result back to ASP handler
        self.suggestionConfirmationFinished.connect(
            self.advanced_search_panel.handleSuggestionConfirmationFinished # NEW
        )
        # Connect item click on suggestion list
        self.suggestions_list.itemClicked.connect(self._handle_suggestion_click)
        # Prevent list from taking focus easily
        self.suggestions_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        app_instance = QApplication.instance()
        if app_instance:
             app_instance.aboutToQuit.connect(self.unload_model_safely)

        self.imageAnalysisSignal.connect(self.update_info_text)
        self.imageInfoSignal.connect(self.update_info_text_with_path)
        self.updateInfoTextSignal.connect(self.update_info_text)
        self.requestImageAnalysis.connect(self.analyze_image_worker) # Connect to worker trigger
        self.thumbnailLoaded.connect(self.set_thumbnail)

        # --- Slideshow Signals ---
        if self.slideshow_button: # Check if UI elements were created
            self.slideshow_button.clicked.connect(self.toggle_slideshow)
        self.slideshow_timer.timeout.connect(self.advance_slideshow)

        # Stop slideshow if underlying data changes significantly
        self.advanced_search_panel.searchRequested.connect(self.stop_slideshow)
        self.sorting_combo.currentIndexChanged.connect(self.stop_slideshow)
        self.sort_order_combo.currentIndexChanged.connect(self.stop_slideshow)
        # Connect the dialog signal that indicates active directories changed
        # We need to find where ManageDirectoriesDialog is instantiated and connect its signal
        # We will connect it when the dialog is opened in `open_manage_directories_dialog`
    
    # --- Helper Method for Context Menu Actions ---
    def search_similar_images(self, image_path: str):
        """Initiates a similarity search based on the provided image path."""
        print(f"ImageGallery: search_similar_images called for: {image_path}")
        # Update the last selected path for context
        self.last_selected_image_path = image_path
        # Display the image in the preview area
        self.display_image_in_preview(image_path)
        # Perform the search, getting tags from DB inside perform_search
        self.perform_search(
            similarity_search=True,
            similar_image_path=image_path
            # Tags will be fetched from DB inside _perform_similarity_search if needed
        )

    # --- Event Handlers ---
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_timer.start(50)

    def keyPressEvent(self, event: QKeyEvent):
        focus_widget = QApplication.focusWidget()
        if isinstance(focus_widget, (QLineEdit, QSpinBox, QTextEdit)):
             super().keyPressEvent(event)
             return
        key = event.key()
        if key == Qt.Key.Key_Left: self.go_to_previous_page()
        elif key == Qt.Key.Key_Right: self.go_to_next_page()
        else: super().keyPressEvent(event)

    def on_splitter_moved(self, pos, index):
        self.splitter_resize_timer.start(50)

    # --- Dialog Management ---
    def open_manage_directories_dialog(self):
        from .dialogs.manage_directories import ManageDirectoriesDialog
        dialog = ManageDirectoriesDialog(self, self.db, self.active_directories, self.threadpool)
        dialog.activeDirectoriesChanged.connect(self.update_active_directories_from_dialog)
        dialog.activeDirectoriesChanged.connect(self.stop_slideshow) # Stop slideshow if dirs change
        dialog.processDirectoriesRequested.connect(self.process_directory)
        dialog.deleteDirectoriesRequested.connect(self.delete_images_from_directory_list)
        dialog.reprocessImagesRequested.connect(self.reprocess_images_action)
        dialog.updateStatusText.connect(self.updateInfoTextSignal.emit)
        dialog.exec()

    @pyqtSlot(set)
    def update_active_directories_from_dialog(self, new_active_set: Set[str]):
        if self.active_directories != new_active_set:
            print(f"Updating active directories from dialog: {new_active_set}")
            self.active_directories = new_active_set
            self.perform_search()
            self.update_suggestions()

    # --- Gallery Display Logic ---
    def arrange_rows(self):
        print("Arrange rows called")
        # --- Clear existing widgets ---
        # Use a safer loop to remove items and ensure proper deletion
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            if item:
                widget = item.widget()
                if widget:
                    widget.setParent(None) # Decouple widget
                    widget.deleteLater()   # Schedule deletion
                # Explicitly delete the layout item itself (though takeAt might suffice)
                # del item # Not usually necessary

        self.thumbnail_loaders.clear()
        # Force processing events to help with immediate clearing if needed
        # QApplication.processEvents()

        if not self.all_images:
            print("No images to display.")
            no_img_label = QLabel("No images found matching your criteria.")
            no_img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.scroll_layout.addWidget(no_img_label)
            # --- Add stretch even when no images to ensure top alignment ---
            self.scroll_layout.addStretch(1)
            # --- End Add ---
            self.update_total_images_label()
            return

        try:
            viewport_width = self.scroll_area.viewport().width()
            scrollbar_width = 0
            # Check scrollbar visibility accurately
            if self.scroll_area.verticalScrollBar().isVisible():
                 scrollbar_width = self.scroll_area.verticalScrollBar().width()

            # Account for layout margins when calculating available width
            margins = self.scroll_layout.contentsMargins()
            available_width = max(1, viewport_width - scrollbar_width - margins.left() - margins.right())

            target_row_height = self.slider.value()

            start_index = (self.current_page - 1) * self.page_size
            end_index = min(start_index + self.page_size, len(self.all_images))
            images_on_page = self.all_images[start_index:end_index]

            if not images_on_page:
                 print("No images for the current page.")
                 no_page_label = QLabel("No images on this page.")
                 no_page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                 self.scroll_layout.addWidget(no_page_label)
                 # --- Add stretch when page is empty ---
                 self.scroll_layout.addStretch(1)
                 # --- End Add ---
                 self.update_total_images_label()
                 return

            current_row_widgets: List[ImageLabel] = []
            current_row_width = 0
            # Define the horizontal spacing used within rows
            h_row_spacing = 10 # Defined in _layout_and_add_row

            for img_path in images_on_page:
                aspect_ratio = self.aspect_ratios.get(img_path)
                if aspect_ratio is None:
                    aspect_ratio = self.get_image_aspect_ratio(img_path)
                    if aspect_ratio is None: continue # Skip if problematic

                img_width_at_target = int(aspect_ratio * target_row_height)
                potential_row_width = current_row_width + img_width_at_target
                # Add horizontal spacing if adding another image to the row
                if current_row_widgets: potential_row_width += h_row_spacing

                # If adding the current image exceeds width, finalize the previous row
                if current_row_widgets and potential_row_width > available_width:
                    # Pass the horizontal spacing to the layout function
                    self._layout_and_add_row(current_row_widgets, available_width, target_row_height, h_row_spacing, is_last_row=False)
                    current_row_widgets = []
                    current_row_width = 0

                # Add the current image to the new/current row
                label = ImageLabel(img_path, self.handle_image_click, self)
                current_row_widgets.append(label)
                current_row_width += img_width_at_target

            # Add the last row if any widgets remain
            if current_row_widgets:
                # Pass the horizontal spacing to the layout function
                self._layout_and_add_row(current_row_widgets, available_width, target_row_height, h_row_spacing, is_last_row=True)

            # --- CHANGE: Add stretch factor at the end of the vertical layout ---
            # This pushes all the added row_frames towards the top.
            self.scroll_layout.addStretch(1)
            # --- END CHANGE ---

            self.update_total_images_label()
            gc.collect() # Keep garbage collection

        except Exception as e:
            print(f"Error during arrange_rows: {e}")
            traceback.print_exc()
            # --- Add stretch even on error ---
            try:
                # Avoid adding widget if layout is being destroyed
                if self.scroll_layout:
                     error_label = QLabel(f"Error arranging images:\n{e}")
                     error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                     self.scroll_layout.addWidget(error_label)
                     self.scroll_layout.addStretch(1)
            except Exception: # Catch potential errors during error handling
                pass
            # --- End Add ---

    # --- CHANGE: Add h_spacing parameter ---
    def _layout_and_add_row(self, row_widgets: List[ImageLabel], available_width: int, target_height: int, h_spacing: int, is_last_row: bool):
    # --- END CHANGE ---
        num_images = len(row_widgets)
        if num_images == 0: return

        total_aspect_ratio = sum(self.aspect_ratios.get(label.image_path, 1.0) for label in row_widgets)
        # Calculate total spacing needed based on the number of gaps
        total_spacing = h_spacing * (num_images - 1) if num_images > 1 else 0

        # Calculate the optimal height to fill the available width
        calculated_height = int((available_width - total_spacing) / total_aspect_ratio) if total_aspect_ratio > 0 else target_height

        # Decide the final height for this row
        final_row_height = calculated_height
        # Prevent the last row from becoming excessively tall if it's short and not the only row
        num_images_on_page = min(self.page_size, len(self.all_images) - (self.current_page - 1) * self.page_size)
        if is_last_row and calculated_height > target_height * 1.2 and num_images_on_page > num_images :
             final_row_height = target_height # Use target height instead of stretching

        # Create the frame and layout for the row
        row_frame = QFrame()
        row_layout = QHBoxLayout(row_frame)
        row_layout.setSpacing(h_spacing) # Use the provided horizontal spacing
        row_layout.setContentsMargins(0, 0, 0, 0) # No margins within the row frame

        total_calculated_width = 0
        for label in row_widgets:
            aspect_ratio = self.aspect_ratios.get(label.image_path, 1.0)
            # Calculate width based on final row height and aspect ratio
            img_width = max(1, int(aspect_ratio * final_row_height))
            img_height = final_row_height

            # Set the fixed size for the ImageLabel
            label.setFixedSize(img_width, img_height)

            # Load thumbnail
            image_id = self.db.get_image_id_from_path(label.image_path)
            if image_id:
                 self.thumbnail_loaders[image_id] = label # Store reference
                 # Create and start the loader
                 loader = ThumbnailLoader(image_id, label.image_path, img_width, img_height, self.thumbnail_cache)
                 loader.signals.thumbnailLoaded.connect(self.thumbnailLoaded.emit)
                 loader.signals.thumbnailError.connect(
                     lambda img_id=image_id, err_msg="": print(f"Thumb load error for {img_id}: {err_msg}")
                 )
                 self.threadpool.start(loader)
            else:
                 # Fallback if image not in DB (should be less common now)
                 label.setText("?")
                 # Provide a default size if aspect ratio was unknown
                 if aspect_ratio is None: label.setFixedSize(final_row_height, final_row_height) # Square fallback

            row_layout.addWidget(label)
            total_calculated_width += label.width() # Use actual width after setting fixed size

        # Add horizontal stretch to the *last row only* if it doesn't fill the width
        if is_last_row and total_calculated_width + total_spacing < available_width:
             row_layout.addStretch(1)

        # Add the completed row frame to the main vertical scroll layout
        self.scroll_layout.addWidget(row_frame)

    @pyqtSlot(str, QPixmap) # Changed from int to str for image_id
    def set_thumbnail(self, image_id: str, pixmap: QPixmap):
        # Lookup using image_id (str)
        if image_id in self.thumbnail_loaders:
            label = self.thumbnail_loaders[image_id] # Get the label object
            if label: # Check if label still exists (it might have been scrolled away)
                # Scale pixmap to fit the label's fixed size
                scaled_pixmap = pixmap.scaled(label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                label.setPixmap(scaled_pixmap)
            # Remove the entry regardless of whether the label still exists
            del self.thumbnail_loaders[image_id]
        else:
            # This warning should ideally not happen now if the key is stable,
            # but we keep it for debugging unexpected scenarios.
            print(f"Warning: Image ID {image_id} not found in loaders dict after thumbnail loaded.")
        # pixmap.detach() # Maybe needed?

    def update_total_images_label(self):
        self.total_images_label.setText(f"Total: {len(self.all_images)}")

    # --- UI Control Handlers ---
    def update_slider_label(self, value):
        """Updates the slider label with the current pixel value."""
        self.slider_label.setText(f"Row Height: {value}px")

    def on_slider_moved(self, value):
        """Handles slider movement for resizing, label update is handled by signal."""
        self.target_height = value
        # self.update_slider_label(value) # No longer needed here
        self.resize_timer.start(200) # Debounce resize

    def on_page_changed(self):
        new_page = self.page_number_edit.value()
        if 1 <= new_page <= self.total_pages and new_page != self.current_page:
            self.current_page = new_page
            self.arrange_rows()
        elif new_page != self.current_page: # Reset if invalid
            self.page_number_edit.setValue(self.current_page)

    def on_page_size_changed(self, value):
        self.page_size = max(1, value)
        self.total_pages = max(1, math.ceil(len(self.all_images) / self.page_size))
        self.total_pages_label.setText(f"of {self.total_pages}")
        self.page_number_edit.setMaximum(self.total_pages)
        self.current_page = max(1, min(self.current_page, self.total_pages))
        self.page_number_edit.setValue(self.current_page)
        self.arrange_rows()

    def go_to_previous_page(self):
        if self.current_page > 1:
            self.page_number_edit.setValue(self.current_page - 1)

    def go_to_next_page(self):
        if self.current_page < self.total_pages:
            self.page_number_edit.setValue(self.current_page + 1)

    def on_sorting_changed(self):
        if self.suppress_search_on_dropdown_update: return
        print(f"Sorting changed to: {self.sorting_combo.currentText()}")
        current_sort = self.sorting_combo.currentText()
        is_random = (current_sort == "Random")
        self.sort_order_combo.setEnabled(not is_random)
        is_similarity = (current_sort == "Similarity")
        self.similarity_mode = is_similarity
        sim_index = self.sorting_combo.findText("Similarity")
        if sim_index != -1: self.sorting_combo.model().item(sim_index).setEnabled(self.similarity_mode)
        if not is_similarity: self.last_selected_image_path = None
        self.perform_search()

    def on_sort_order_changed(self):
        if self.suppress_search_on_dropdown_update: return
        print(f"Sort order changed to: {self.sort_order_combo.currentText()}")
        self.perform_search()

    # --- Image Interaction & Info Display ---
    def handle_image_click(self, img_path: str, analyze: bool = False):
        print(f"ImageGallery: Handling click for image: {img_path}, analyze={analyze}")
        self.last_selected_image_path = img_path
        self.display_image_in_preview(img_path)
        self.process_image_info(img_path, analyze=analyze)

    @pyqtSlot(str)
    def display_image_in_preview(self, img_path: str, target_label: Optional[QWidget] = None): # target_label optional now
        """Loads the image in a worker and sets it in the DragDropArea."""
        # If a target_label is passed AND it's our DragDropArea, use it. Otherwise, use self.drag_drop_area.
        # This maintains compatibility if the method was called elsewhere, but primarily targets self.drag_drop_area.
        target_view = self.drag_drop_area if target_label is None or target_label == self.drag_drop_area else None

        if not target_view:
             print("ImageGallery: display_image_in_preview - Invalid target.")
             return

        print(f"ImageGallery: Displaying image in preview target: {img_path}")
        # Optionally show "Loading..." text (though set_image handles placeholder)
        # target_view.set_image(None) # Clear previous and show placeholder

        def load_and_display_task():
            try:
                # Load using QPixmap for direct use in QGraphicsView
                pixmap = QPixmap(img_path)
                # Return the pixmap (or None if loading failed)
                return pixmap
            except Exception as e:
                print(f"Error loading image for preview {img_path}: {e}")
                return None # Return None on error

        def handle_load_result(pixmap_result: Optional[QPixmap]):
            load_successful = pixmap_result and not pixmap_result.isNull()
            if target_view: # Check if target_view is still valid
                if load_successful:
                    target_view.set_image(pixmap_result)
                else:
                    print(f"ImageGallery: Could not load image: {img_path}")
                    target_view.set_image(None) # Show placeholder on failure
            else:
                 print("ImageGallery: Target view no longer valid after image load.")

            # --- Slideshow Timer Logic ---
            if self.is_slideshow_active:
                if load_successful:
                    delay_ms = (self.slideshow_delay_spinbox.value() * 1000) if self.slideshow_delay_spinbox else 5000
                    print(f"  Slideshow: Image loaded, starting timer for {delay_ms}ms")
                    self.slideshow_timer.start(delay_ms)
                else:
                    # Image failed to load, advance quickly
                    print(f"  Slideshow: Image failed to load, advancing quickly.")
                    QTimer.singleShot(100, self.advance_slideshow) # Advance after 100ms
            # --- End Slideshow Timer Logic ---

        worker = Worker(load_and_display_task)
        # Connect the worker's finished signal to handle the result
        worker.signals.finished.connect(handle_load_result)
        # Optionally connect error signal for more specific error handling
        # worker.signals.error.connect(...)
        self.threadpool.start(worker)

    def process_image_info(self, img_path: str, analyze: bool, store_temp_predictions_callback: Optional[Callable] = None):
        """
        Displays image info from DB or triggers analysis in a worker.

        Args:
            img_path: Path to the image file.
            analyze: If True, perform ML analysis; otherwise, fetch from DB.
            store_temp_predictions_callback: Optional callback to store predictions (used for drag-drop).
        """
        print(f"ImageGallery: Processing image info: {img_path}, analyze={analyze}")
        self.info_text.clear()
        self._temp_pred_callback = store_temp_predictions_callback # Store callback

        if analyze:
            print(f"ImageGallery: Requesting analysis for: {img_path}")
            # --- CHANGE: Emit image path instead of PIL Image object ---
            self.requestImageAnalysis.emit(img_path) # Trigger worker with path
            # --- END CHANGE ---
        else:
            self.display_image_info_from_db(img_path)
            if self._temp_pred_callback: self._temp_pred_callback(None) # No predictions from DB path
            self._temp_pred_callback = None

    @pyqtSlot(str) # Accept image path (str)
    def analyze_image_worker(self, image_path: str):
        """Slot connected to requestImageAnalysis signal, runs analysis in worker."""
        worker = Worker(self._analyze_image_task, image_path=image_path)
        # --- CHANGE: Connect to the 'finished' signal instead of 'result' ---
        worker.signals.finished.connect(self._handle_analysis_result)
        # --- END CHANGE ---
        worker.signals.error.connect(self._handle_analysis_error)
        self.threadpool.start(worker)

    # --- CHANGE: Accept image_path, load image inside ---
    def _analyze_image_task(self, image_path: str) -> Tuple[str, Optional[List[TagPrediction]], Optional[str]]:
    # --- END CHANGE ---
        """The actual analysis task run by the worker."""
        # --- CHANGE: Load image inside the worker task ---
        try:
            with Image.open(image_path) as image:
                # Ensure model is loaded
                if self.model.tagger is None:
                    self.model.load_model() # Load if needed

                if self.model.tagger is None: # Check again if loading failed
                    raise RuntimeError("Model could not be loaded for analysis.")

                predictions = self.model.predict(image)
                rating = self.model.determine_rating(predictions)
                info_text = self._format_image_info(image_path, rating, predictions)
                return info_text, predictions, image_path
        except (FileNotFoundError, UnidentifiedImageError, Exception) as e:
            print(f"Error during image analysis task for {image_path}: {e}")
            traceback.print_exc()
            # Return error info in the expected tuple format
            return f"Error analyzing image {os.path.basename(image_path)}:\n{e}", None, image_path
        # --- END CHANGE ---

    @pyqtSlot(object) # Receives result from worker signal (tuple)
    def _handle_analysis_result(self, result_data: Tuple[str, Optional[List[TagPrediction]], Optional[str]]):
        """Handles the result of image analysis from the worker."""
        info_text, predictions, img_path = result_data

        # --- CHANGE: Ensure info text is updated even on error ---
        self.updateInfoTextSignal.emit(info_text) # Update UI text (will show error if analysis failed)
        # --- END CHANGE ---

        if predictions is None or img_path is None: # Indicates an error occurred during analysis
             if self._temp_pred_callback: self._temp_pred_callback(None)
             self._temp_pred_callback = None
             return # Don't proceed with DB add or callback execution

        # Handle DB update or temporary storage
        normalized_path = self.db.normalize_path(img_path)
        is_dropped_image = (self.drag_drop_area.dropped_image_path and
                            self.db.normalize_path(self.drag_drop_area.dropped_image_path) == normalized_path)

        if is_dropped_image and self._temp_pred_callback:
            print(f"Storing temporary predictions for dropped image: {img_path}")
            # --- CHANGE: Ensure callback receives predictions ---
            self._temp_pred_callback(predictions)
            # --- END CHANGE ---
        elif not is_dropped_image: # Only add to DB if not the currently dropped image
            print(f"Adding/Updating image in database: {img_path}")
            try:
                # Consider running DB update in a worker if it causes noticeable lag
                self.db.add_image(img_path, predictions, self.model)
            except Exception as db_err:
                print(f"Error adding image {img_path} to database: {db_err}")
                self.updateInfoTextSignal.emit(f"\nError saving tags to DB: {db_err}")

        # --- CHANGE: Clear callback only after potentially calling it ---
        if not is_dropped_image: # Clear callback if it wasn't the dropped image case
            self._temp_pred_callback = None
        # --- END CHANGE ---
        # Note: For the dropped image case, the callback is cleared within set_temporary_predictions


    @pyqtSlot(tuple)
    def _handle_analysis_error(self, error_info: tuple):
        """Handles errors reported by the analysis worker."""
        exception, traceback_str = error_info
        print(f"Image analysis worker failed: {exception}", file=sys.stderr)
        self.updateInfoTextSignal.emit(f"Error during analysis: {exception}\n")
        if self._temp_pred_callback: self._temp_pred_callback(None) # Signal error
        self._temp_pred_callback = None


    def display_image_info_from_db(self, img_path: str):
        print(f"ImageGallery: Retrieving image info from database: {img_path}")
        try:
            rating, tags = self.db.get_image_info_by_path(img_path)
            if rating is not None and tags is not None:
                info_text_content = self._format_image_info(img_path, rating, tags)
                self.imageInfoSignal.emit(info_text_content, img_path)
            else:
                info_text = f"Name: {os.path.basename(img_path)}\n(Not found in database)"
                try:
                     if Path(img_path).is_file(): info_text += f"\nFile Size: {human_readable_size(os.path.getsize(img_path))}"
                except Exception: pass
                self.imageInfoSignal.emit(info_text, img_path)
        except Exception as e:
            print(f"Error retrieving DB info for {img_path}: {e}")
            traceback.print_exc()
            self.imageInfoSignal.emit(f"Error retrieving info:\n{e}", img_path)

    def _format_image_info(self, img_path: str, rating: str, tags: List[TagPrediction]) -> str:
        info_text = ""
        try:
            if Path(img_path).is_file():
                file_size = os.path.getsize(img_path)
                mod_time = os.path.getmtime(img_path)
                date_str = datetime.datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M')
                try:
                    with Image.open(img_path) as img:
                        width, height = img.size
                        aspect_ratio = width / height if height != 0 else 1.0
                        res_str = f"{width}x{height}"
                except Exception: res_str, aspect_ratio = "N/A", 1.0
                info_text += f"Name: {os.path.basename(img_path)}\n"
                info_text += f"Size: {human_readable_size(file_size)}\n"
                info_text += f"Resolution: {res_str}\n"
                info_text += f"Aspect Ratio: {aspect_ratio:.2f}\n"
                info_text += f"Date: {date_str}\n\n"
            else: info_text += f"Name: {os.path.basename(img_path)} (File not found)\n\n"

            rating_tag = next((t for t in tags if t.category.lower() == 'rating' and t.tag.lower() == rating.lower()), None)
            rating_conf = rating_tag.confidence if rating_tag else 0.0
            rating_str = f"{rating} ({rating_conf:.1%}) [rating]"
            char_tags = sorted([t for t in tags if t.category.lower() == 'character'], key=lambda t: t.confidence, reverse=True)
            char_str = ', '.join(f"{t.tag} ({t.confidence:.0%})" for t in char_tags)
            info_text += f"{char_str} [character]\n{rating_str}\n\n" if char_str else f"{rating_str}\n\n"
            general_tags = sorted([t for t in tags if t.category.lower() == 'general'], key=lambda t: t.confidence, reverse=True)
            for tag in general_tags: info_text += f"{tag.tag} ({tag.confidence:.1%}) [general]\n"
        except Exception as e:
            print(f"Error formatting image info for {img_path}: {e}")
            info_text += f"\nError formatting info: {e}"
        return info_text

    @pyqtSlot(str)
    def update_info_text(self, text: str):
        current_text = self.info_text.toPlainText()
        max_lines = 500
        lines = current_text.splitlines()
        if len(lines) > max_lines:
             current_text = "\n".join(lines[-max_lines:]) + "\n"
             self.info_text.setPlainText(current_text)
        self.info_text.moveCursor(QTextCursor.MoveOperation.End)
        self.info_text.insertPlainText(text)
        self.info_text.moveCursor(QTextCursor.MoveOperation.End)

    @pyqtSlot(str, str)
    def update_info_text_with_path(self, text: str, image_path: str):
        self.info_text.setPlainText(text)
        self.info_text.moveCursor(QTextCursor.MoveOperation.Start)
        # self.last_selected_image_path = image_path # Context updated elsewhere

    # --- Directory Processing Slots ---
    @pyqtSlot(list)
    def process_directory(self, directories: List[str]):
        print(f"Received request to process directories: {directories}")
        if self.is_processing:
            QMessageBox.information(self, "Processing Busy", "Already processing directories. Please wait.")
            return
        if not directories:
            QMessageBox.warning(self, "No Directories", "No directories specified for processing.")
            return

        self.is_processing = True
        self.processed_directories = []
        self.total_images_to_process = 0
        self.processed_images_count = 0
        self.updateInfoTextSignal.emit("Starting directory processing...\n")
        self.set_ui_enabled(False)

        all_image_paths = []
        for directory in directories:
            if os.path.isdir(directory):
                try:
                    paths_in_dir = [os.path.join(directory, f) for f in os.listdir(directory) if f.lower().endswith(config.SUPPORTED_FORMATS)]
                    all_image_paths.extend(paths_in_dir)
                except OSError as e: print(f"Error listing directory {directory}: {e}")
            else: print(f"Directory not found or invalid: {directory}")

        images_to_process_paths = self._filter_paths_for_processing(all_image_paths)
        self.total_images_to_process = len(images_to_process_paths)
        print(f"Total images requiring processing: {self.total_images_to_process}")
        self.updateInfoTextSignal.emit(f"Found {self.total_images_to_process} new/modified images to process.\n")

        if self.total_images_to_process == 0:
             self.on_processing_finished()
             return

        image_queue: Queue[str] = Queue()
        for path in images_to_process_paths: image_queue.put(path)

        worker = Worker(self.process_image_queue, image_queue=image_queue, status_callback=self.updateInfoTextSignal.emit)
        worker.signals.finished.connect(self.on_processing_finished)
        worker.signals.error.connect(self.on_processing_error)
        self.threadpool.start(worker)

    def _filter_paths_for_processing(self, paths: List[str]) -> List[str]:
        """Checks DB (mod time, size) to find paths needing processing."""
        paths_to_process = []
        print(f"Filtering {len(paths)} potential paths for processing...")
        checked_count = 0
        try:
             with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                  cursor = conn.cursor()
                  for img_path in paths:
                       checked_count += 1
                       if checked_count % 500 == 0: # Print progress
                           print(f"  Checked {checked_count}/{len(paths)} paths...")

                       needs_processing = True # Assume needs processing unless proven otherwise
                       try:
                            # Get current file info first
                            current_mod_time = os.path.getmtime(img_path)
                            current_file_size = os.path.getsize(img_path)

                            # Check database
                            normalized_path = self.db.normalize_path(img_path)
                            # --- MODIFICATION HERE: Select size, use COLLATE NOCASE ---
                            cursor.execute("SELECT modification_time, file_size FROM images WHERE path = ? COLLATE NOCASE", (normalized_path,))
                            result = cursor.fetchone()

                            if result:
                                stored_mod_time, stored_file_size = result
                                # Check if mod time AND size match (within tolerance for time)
                                time_matches = abs(current_mod_time - stored_mod_time) <= 1 if stored_mod_time is not None else False
                                size_matches = current_file_size == stored_file_size if stored_file_size is not None else False

                                if time_matches and size_matches:
                                    needs_processing = False # Skip if both time and size match
                            # else: image not in DB, needs_processing remains True

                       except FileNotFoundError:
                            print(f"  Skipping missing file during filter: {img_path}")
                            needs_processing = False # Cannot process if file doesn't exist
                       except OSError as e:
                            print(f"  Error accessing file during filter {img_path}: {e}")
                            # Decide whether to process or skip on error; skipping is safer
                            needs_processing = False
                       except sqlite3.Error as e:
                            print(f"  DB error during filter for {img_path}: {e}")
                            # If DB error, assume processing is needed to be safe? Or skip?
                            # Let's assume processing is needed if DB check fails.
                            needs_processing = True

                       if needs_processing:
                            paths_to_process.append(img_path)

        except sqlite3.Error as e:
             print(f"DB error during bulk filter setup: {e}")
             # On major DB error, maybe process all as a fallback? Or none?
             # Returning all might lead to excessive processing. Let's return empty.
             return [] # Return empty list on major DB error

        print(f"Filtering complete. {len(paths_to_process)} paths require processing.")
        return paths_to_process

    def process_image_queue(self, image_queue: Queue[str], status_callback: Optional[Callable[[str], None]] = None):
        print("Worker starting to process image queue...")
        model_loaded = False
        try:
            if self.model.tagger is None:
                 self.model.load_model()
                 model_loaded = True
                 if status_callback: status_callback("Model loaded.\n")
        except Exception as e:
             if status_callback: status_callback(f"FATAL: Error loading model: {e}\nProcessing aborted.\n")
             print(f"FATAL: Error loading model in worker: {e}")
             # How to signal fatal error back? Raise exception?
             raise RuntimeError(f"Model loading failed: {e}") from e

        while not image_queue.empty():
            img_path = image_queue.get_nowait()
            try:
                if not Path(img_path).is_file():
                     if status_callback: status_callback(f"Skipping missing: {os.path.basename(img_path)}\n")
                     self.processed_images_count += 1
                     continue

                if status_callback: status_callback(f"Processing: {os.path.basename(img_path)}...")

                with Image.open(img_path) as image:
                    predictions = self.model.predict(image)
                # DB add handles update logic and uses its own connection/lock
                self.db.add_image(img_path, predictions, self.model)

                self.processed_images_count += 1
                progress = (self.processed_images_count / self.total_images_to_process) * 100 if self.total_images_to_process > 0 else 0
                status_str = f" OK ({self.processed_images_count}/{self.total_images_to_process} - {progress:.1f}%)\n"
                if status_callback: status_callback(status_str)

            except (FileNotFoundError, UnidentifiedImageError, Exception) as e:
                 self.processed_images_count += 1
                 error_str = f" Error processing {os.path.basename(img_path)}: {e}\n"
                 if status_callback: status_callback(error_str)
                 print(error_str.strip())
                 # Don't print full traceback for common errors like corrupt images
                 if not isinstance(e, (FileNotFoundError, UnidentifiedImageError)):
                      traceback.print_exc()
            finally:
                image_queue.task_done()

        print("Worker finished processing image queue.")
        # No return needed, signals handle completion/error


    def on_processing_finished(self):
        print("All directory processing finished.")
        self.is_processing = False
        self.set_ui_enabled(True)
        try:
            if status_callback:=getattr(self, 'updateInfoTextSignal', None): status_callback.emit("Cleaning database...\n")
            self.db.cleanup_database()
            if status_callback: status_callback.emit("Vacuuming database...\n")
            self.db.vacuum_database()
            if status_callback: status_callback.emit("Database maintenance complete.\n")
        except Exception as e:
             print(f"Error during post-processing DB maintenance: {e}")
             if status_callback: status_callback.emit(f"Error during DB maintenance: {e}\n")
        self.perform_search()
        self.update_suggestions()
        self.updateInfoTextSignal.emit("Directory processing complete. Gallery updated.\n")

    def on_processing_error(self, error_info: tuple):
        exception, traceback_str = error_info
        print(f"Directory processing worker failed: {exception}", file=sys.stderr)
        self.is_processing = False
        self.set_ui_enabled(True)
        self.updateInfoTextSignal.emit(f"Error during directory processing: {exception}\n")
        QMessageBox.critical(self, "Processing Error", f"An error occurred during directory processing:\n{exception}")

    @pyqtSlot(list)
    def delete_images_from_directory_list(self, directories: List[str]):
        print(f"Received request to delete directories: {directories}")
        if not directories: return
        self.set_ui_enabled(False)
        self.updateInfoTextSignal.emit(f"Starting deletion for {len(directories)} directories...\n")
        # Run deletion in worker to avoid blocking UI
        worker = Worker(self._delete_dirs_task, directories=directories)
        worker.signals.finished.connect(self._handle_deletion_finished)
        worker.signals.error.connect(self._handle_deletion_error)
        worker.signals.update_info_text.connect(self.updateInfoTextSignal.emit)
        self.threadpool.start(worker)

    def _delete_dirs_task(self, directories: List[str], status_callback: Optional[Callable[[str], None]] = None) -> int:
        """Worker task for deleting directories."""
        total_deleted = 0
        for directory in directories:
            normalized_directory = self.db.normalize_path(directory)
            if not normalized_directory.endswith('/'): normalized_directory += '/'
            count = 'N/A'
            try:
                 with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                      cursor = conn.cursor()
                      cursor.execute("SELECT COUNT(*) FROM images WHERE path LIKE ?", (f"{normalized_directory}%",))
                      count = cursor.fetchone()[0]
            except sqlite3.Error: pass # Ignore count error

            if status_callback: status_callback(f"Deleting {count} images from {directory}...\n")
            self.db.delete_images_in_directory(directory) # Handles DB and thumbnails
            total_deleted += count if isinstance(count, int) else 0

        # Cleanup after all deletions
        if status_callback: status_callback("Cleaning database after deletions...\n")
        self.db.cleanup_database()
        if status_callback: status_callback("Vacuuming database...\n")
        self.db.vacuum_database()
        return total_deleted

    @pyqtSlot(object) # Receives total_deleted count
    def _handle_deletion_finished(self, total_deleted: int):
        self.updateInfoTextSignal.emit(f"Deletion complete. Removed {total_deleted} image entries.\n")
        self.set_ui_enabled(True)
        self.perform_search()
        self.update_suggestions()

    @pyqtSlot(tuple)
    def _handle_deletion_error(self, error_info: tuple):
        exception, traceback_str = error_info
        print(f"Directory deletion worker failed: {exception}", file=sys.stderr)
        self.set_ui_enabled(True)
        self.updateInfoTextSignal.emit(f"Error during deletion: {exception}\n")
        QMessageBox.critical(self, "Deletion Error", f"An error occurred during deletion:\n{exception}")

    @pyqtSlot(list, dict)
    def reprocess_images_action(self, image_ids: List[str], properties: Dict[str, bool]):
        print(f"Received request to reprocess {len(image_ids)} images. Properties: {properties}")
        if not image_ids or not any(properties.values()): return
        self.set_ui_enabled(False)
        self.updateInfoTextSignal.emit(f"Starting reprocessing for {len(image_ids)} images...\n")
        tasks = [(img_id, properties) for img_id in image_ids]
        task_queue: Queue[Tuple[str, Dict[str, bool]]] = Queue()
        for task in tasks: task_queue.put(task)
        self.total_images_to_process = len(tasks)
        self.processed_images_count = 0
        worker = Worker(self.reprocess_image_queue, task_queue=task_queue, status_callback=self.updateInfoTextSignal.emit)
        worker.signals.finished.connect(self.on_reprocessing_finished)
        worker.signals.error.connect(self.on_reprocessing_error)
        self.threadpool.start(worker)

    def reprocess_image_queue(self, task_queue: Queue[Tuple[str, Dict[str, bool]]], status_callback: Optional[Callable[[str], None]] = None):
        print("Worker starting image reprocessing queue...")
        model_loaded = False
        while not task_queue.empty():
            image_id, properties = task_queue.get_nowait()
            path: Optional[str] = None
            try:
                 # Get path inside the loop for each ID
                 try:
                      with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                           cursor = conn.cursor()
                           cursor.execute("SELECT path FROM images WHERE id = ?", (image_id,))
                           result = cursor.fetchone()
                           if result: path = result[0]
                 except sqlite3.Error as db_err:
                      if status_callback: status_callback(f"DB Error getting path for {image_id}: {db_err}\n")
                      self.processed_images_count += 1; task_queue.task_done(); continue

                 if not path or not Path(path).is_file():
                     if status_callback: status_callback(f"Skipping missing/invalid path for {image_id}: {path}\n")
                     self.processed_images_count += 1; task_queue.task_done(); continue

                 basename = os.path.basename(path)
                 status_msg_prefix = f"Reprocessing {basename}:"
                 update_parts = [] # To collect parts of the UPDATE query for metadata
                 update_params = [] # To collect params for the UPDATE query

                 if properties.get("tags"):
                     if not model_loaded:
                          try: self.model.load_model(); model_loaded = True
                          except Exception as model_err:
                               if status_callback: status_callback(f" Model Load Error: {model_err}"); raise model_err # Propagate model load error
                     try:
                          with Image.open(path) as img: predictions = self.model.predict(img)
                          self.db.add_image(path, predictions, self.model) # add_image handles update logic
                          if status_callback: status_callback(" Tags ✓")
                     except Exception as tag_err:
                          if status_callback: status_callback(f" Tags Error: {tag_err}")

                 if properties.get("thumbnail"):
                     try:
                          self.thumbnail_cache.update_thumbnail(path, image_id)
                          if status_callback: status_callback(" Thumb ✓")
                     except Exception as thumb_err:
                          if status_callback: status_callback(f" Thumb Error: {thumb_err}")

                 # --- Handle Metadata Updates ---
                 needs_metadata_update = False
                 try:
                      current_mod_time = os.path.getmtime(path) if properties.get("mod_time") else None
                      current_size = os.path.getsize(path) if properties.get("file_size") else None
                      current_res = None
                      if properties.get("resolution"):
                           try:
                                with Image.open(path) as img: current_res = f"{img.width}x{img.height}"
                           except Exception as img_err:
                                if status_callback: status_callback(f" Res Error (PIL): {img_err}")

                      if current_mod_time is not None:
                           update_parts.append("modification_time = ?")
                           update_params.append(current_mod_time)
                           needs_metadata_update = True
                      if current_size is not None:
                           update_parts.append("file_size = ?")
                           update_params.append(current_size)
                           needs_metadata_update = True
                      if current_res is not None:
                           update_parts.append("resolution = ?")
                           update_params.append(current_res)
                           needs_metadata_update = True

                      # Perform the combined metadata update if needed
                      if needs_metadata_update:
                           update_params.append(image_id) # Add image_id for WHERE clause
                           sql = f"UPDATE images SET {', '.join(update_parts)} WHERE id = ?"
                           with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                                conn.execute(sql, update_params)
                           if status_callback: status_callback(" Meta ✓")

                 except Exception as meta_err:
                      needs_metadata_update = False # Ensure we don't log success
                      if status_callback: status_callback(f" Meta Error (FS/DB): {meta_err}")
                 # --- End Metadata Updates ---

                 # Final status update for the image
                 self.processed_images_count += 1
                 progress = (self.processed_images_count / self.total_images_to_process) * 100 if self.total_images_to_process > 0 else 0
                 if status_callback: status_callback(f" ({self.processed_images_count}/{self.total_images_to_process} - {progress:.1f}%)\n")

            except Exception as e:
                 # Catch errors not handled within specific property checks (like model load)
                 self.processed_images_count += 1
                 error_str = f"Error reprocessing {image_id} ({path}): {e}\n"
                 if status_callback: status_callback(error_str)
                 print(error_str.strip()); traceback.print_exc()
            finally:
                if status_callback: # Ensure prefix is always added if callback exists
                     # Prepend prefix to any status messages emitted within the loop
                     # Note: This might prepend multiple times if status is emitted incrementally
                     # A better approach might be to build the full status line before emitting.
                     # Let's emit the prefix here instead.
                     status_callback(status_msg_prefix)
                task_queue.task_done()
        print("Worker finished reprocessing image queue.")

    def on_reprocessing_finished(self):
        print("Image reprocessing finished.")
        self.set_ui_enabled(True)
        self.updateInfoTextSignal.emit("Image reprocessing complete.\n")
        self.perform_search()

    def on_reprocessing_error(self, error_info: tuple):
        exception, traceback_str = error_info
        print(f"Image reprocessing worker failed: {exception}", file=sys.stderr)
        self.set_ui_enabled(True)
        self.updateInfoTextSignal.emit(f"Error during image reprocessing: {exception}\n")
        QMessageBox.critical(self, "Reprocessing Error", f"An error occurred during reprocessing:\n{exception}")

    def _handle_tag_segment_selected(self, selected_tag: str):
        """
        Handles the selection of a full tag segment (e.g., via double-click).
        Updates the suggestion list to show all available tags (unfiltered).
        """
        print(f"ImageGallery: Tag segment selected: '{selected_tag}', updating suggestions to show all.")

        # Stop any pending suggestion timer that might have been triggered
        # by the programmatic selection change.
        self.suggestion_timer.stop()

        # Ensure the search field still has focus, otherwise suggestions won't show
        if not self.advanced_search_panel.search_field.hasFocus():
            print("  Search field lost focus, skipping suggestion update.")
            return

        # Fetch all suggestions (empty search term)
        self._suggestions_map.clear()
        try:
            # Use empty search term to get top/all tags
            tags_with_counts = self.db.get_matching_tags_for_directories(
                desired_dirs=list(self.active_directories), undesired_dirs=[],
                desired_tags=[], undesired_tags=[],
                search_term="", # <-- Force empty search term
                limit=100 # Keep limit reasonable
            )
            self._suggestions_map = {f"{tag} ({count})": tag for tag, count in tags_with_counts}
        except Exception as e:
            print(f"Error fetching all suggestions: {e}")
            self._suggestions_map.clear()

        # Update the list widget
        self.suggestions_list.clear()
        if self._suggestions_map:
            self.suggestions_list.addItems(self._suggestions_map.keys())
            self.suggestions_list.setCurrentRow(-1) # Ensure nothing selected initially

        self._update_suggestion_list_visibility()

    # --- Search Logic ---
    def update_suggestions(self):
        """Fetches suggestions based on current query/cursor and updates the main suggestion list."""
        print("ImageGallery: update_suggestions called")

        search_field_has_focus = self.advanced_search_panel.search_field.hasFocus()
        text = self.advanced_search_panel.get_current_query()
        cursor_pos = self.advanced_search_panel.get_cursor_position()

        # --- Logic to find the term being typed (KEEP PREVIOUS VERSION) ---
        # (Copied from your provided code)
        start_pos = cursor_pos
        # Find start of current word/segment (adjust logic if needed for operators)
        delimiters = [r'\bAND\b', r'\bOR\b', r'\bNOT\b', r'\[', r'\]'] # Added brackets
        delimiter_regex = re.compile('|'.join(delimiters), re.IGNORECASE)
        found_boundary = False
        check_pos = start_pos - 1
        while check_pos >= 0:
            # Check for explicit boundaries first
            if text[check_pos] in "[]":
                start_pos = check_pos + 1
                found_boundary = True
                break
            # Check for operators preceded by space or start
            # Look for a space, then check backwards for operator
            possible_op_start = -1
            if text[check_pos].isspace():
                # Check for 'AND '
                if check_pos >= 3 and text[check_pos-3:check_pos].upper() == "AND": possible_op_start = check_pos-3
                # Check for 'OR '
                elif check_pos >= 2 and text[check_pos-2:check_pos].upper() == "OR": possible_op_start = check_pos-2
                # Check for 'NOT '
                elif check_pos >= 3 and text[check_pos-3:check_pos].upper() == "NOT": possible_op_start = check_pos-3

                if possible_op_start != -1:
                    # Ensure it's a whole word operator (preceded by space or start of string or bracket)
                    is_start_boundary = (possible_op_start == 0 or text[possible_op_start-1].isspace() or text[possible_op_start-1] in "[]")
                    if is_start_boundary:
                        start_pos = check_pos + 1 # Start after the space following the operator
                        found_boundary = True
                        break
            check_pos -= 1
        if not found_boundary: # If no boundary found searching backwards, start from beginning
            start_pos = 0
        current_term = text[start_pos:cursor_pos].strip() # Get the segment and strip spaces
        # --- End Logic to find the term ---

        print(f"  Term being typed: '{current_term}' (from pos {start_pos} to {cursor_pos})")
        self._suggestions_map.clear() # Use the main window's map

        # --- Fetch suggestions (only if field still has focus) ---
        # Re-check focus before hitting the database.
        if search_field_has_focus and self.active_directories:
            try:
                tags_with_counts = self.db.get_matching_tags_for_directories(
                    desired_dirs=list(self.active_directories), undesired_dirs=[],
                    desired_tags=[], undesired_tags=[],
                    search_term=current_term,
                    limit=100 # Keep limit reasonable
                )
                # Populate the main window's suggestion map
                self._suggestions_map = {f"{tag} ({count})": tag for tag, count in tags_with_counts}
            except Exception as e:
                print(f"Error fetching suggestions: {e}")
                self._suggestions_map.clear()
        else:
            # Clear map if focus lost before DB query or no active dirs
             self._suggestions_map.clear()

        # Update the list widget
        self.suggestions_list.clear()

        if self._suggestions_map:
            self.suggestions_list.addItems(self._suggestions_map.keys())
            self.suggestions_list.setCurrentRow(-1) # Ensure nothing selected initially

        self._update_suggestion_list_visibility()
    
    @pyqtSlot()
    def _handle_check_suggestion_visibility(self):
        """Handler for ASP's request for suggestion visibility info."""
        is_visible = self.suggestions_list.isVisible()
        count = self.suggestions_list.count()
        # Emit the info back to ASP
        self.suggestionVisibilityInfo.emit(is_visible, count)

    @pyqtSlot(str)
    def handleNavigateSuggestions(self, direction: str):
        """Handles up/down navigation, just visually highlighting the item."""
        if not self.suggestions_list.isVisible():
            return

        count = self.suggestions_list.count()
        if count == 0:
            return

        current_row = self.suggestions_list.currentRow()
        next_row = -1

        if direction == 'down':
            next_row = (current_row + 1) % count
        elif direction == 'up':
            if current_row <= 0:
                 next_row = count - 1
            else:
                 next_row = current_row - 1

        if next_row != -1: # Allow setting row even if it's the same (e.g., single item list)
            print(f"IG: Navigating to row {next_row}") # Debug
            self.suggestions_list.setCurrentRow(next_row) # Highlight visually
    
    @pyqtSlot()
    def _confirm_selected_suggestion(self):
        """
        Confirms the currently highlighted suggestion, updates the search field,
        and hides the suggestion list.
        """
        print("IG: Received confirmSuggestion request.")
        current_row = self.suggestions_list.currentRow()
        item = self.suggestions_list.currentItem()
        confirmation_happened = False # Flag to track outcome

        if current_row != -1 and item:
            display_text = item.text()
            actual_tag = self._suggestions_map.get(display_text)
            if actual_tag:
                print(f"  Confirming selection: '{actual_tag}'")
                self.advanced_search_panel.insert_suggestion(actual_tag)
                QTimer.singleShot(0, self.update_suggestions) # Update list based on new text
                confirmation_happened = True # Mark as confirmed
            else:
                print(f"  Error: Could not find actual tag for '{display_text}' on confirm.")
        else:
            print("  Confirm request received but no item selected in list.")
        
        # Emit the result *after* processing
        print(f"  Emitting suggestionConfirmationFinished({confirmation_happened})")
        self.suggestionConfirmationFinished.emit(confirmation_happened)
    
    def _update_suggestion_list_visibility(self):
        """Central method to show/hide list and emit state."""
        should_show = bool(self._suggestions_map) and self.advanced_search_panel.search_field.hasFocus()
        current_visibility = self.suggestions_list.isVisible()

        if should_show and not current_visibility:
            print("  Showing suggestions list.") # Keep debug
            self.suggestions_list.show()
        elif not should_show and current_visibility:
            print("  Hiding suggestions list.") # Keep debug
            self.suggestions_list.hide()

        # Emit current state AFTER potential change
        self.suggestionVisibilityInfo.emit(self.suggestions_list.isVisible(), self.suggestions_list.count())
    
    @pyqtSlot()
    def _hide_suggestions(self):
        """Slot to hide the suggestions list and emit state."""
        if self.suggestions_list.isVisible():
            print("ImageGallery: Hiding suggestions.")
            self.suggestions_list.hide()
        self.suggestionVisibilityInfo.emit(False, 0) # Hidden, count is irrelevant but 0 is safe
    
    def _show_suggestions(self):
        """
        Slot to show/update the suggestions list, typically called on focus gain.
        Uses a zero timer to ensure cursor position is updated first.
        """
        print("ImageGallery: Focus gained, setting flag and scheduling suggestions update.") # Debug

        # Set the flag to ignore the next inputChanged signal caused by focus gain
        self._ignore_cursor_change_on_focus = True

        # Stop any pending suggestion timer from previous interactions
        self.suggestion_timer.stop()

        # Use a 0ms timer to allow the event loop to process potential cursor updates
        # *before* update_suggestions is called.
        QTimer.singleShot(0, self.update_suggestions)

    @pyqtSlot(QListWidgetItem)
    def _handle_suggestion_click(self, item: QListWidgetItem):
        """Handles clicks on the main suggestion list."""
        display_text = item.text()
        actual_tag = self._suggestions_map.get(display_text)
        if actual_tag:
            self.advanced_search_panel.insert_suggestion(actual_tag)
        # --- Ensure list is hidden after click ---
        if self.suggestions_list.isVisible():
            self.suggestions_list.hide()
        # --- End Ensure ---

    def on_text_or_cursor_changed(self):
        """Slot connected to inputChanged signal from AdvancedSearchPanel."""

        # Check if the flag is set (meaning focus was just gained)
        if self._ignore_cursor_change_on_focus:
            # Reset the flag and do nothing else this time
            self._ignore_cursor_change_on_focus = False
            print("ImageGallery: Ignoring first input change after focus.") # Debug
            return

        # Debounce suggestion updates when typing (only runs if flag was false)
        print("ImageGallery: Input changed, starting suggestion timer.") # Debug
        self.suggestion_timer.start(50)

    @pyqtSlot(str) # Connected to AdvancedSearchPanel.searchRequested
    def perform_search(self, search_query: Optional[str] = None, similarity_search: bool = False, similar_image_path: Optional[str] = None, tags: Optional[List[TagPrediction]] = None):
        print(f"ImageGallery: Entering perform_search")
        # self._hide_suggestions() # Call the dedicated hide slot
        print(f"  Args: search_query='{search_query}', similarity_search={similarity_search}, similar_image_path='{similar_image_path}', tags_provided={tags is not None}")
        print(f"  State: active_dirs={self.active_directories}, similarity_mode={self.similarity_mode}, last_selected='{self.last_selected_image_path}'")

        if search_query is None: search_query = self.advanced_search_panel.get_current_query().strip()
        print(f"  Effective search_query: '{search_query}'")

        current_sort_is_similarity = (self.sorting_combo.currentText() == "Similarity")
        if similarity_search:
             if not similar_image_path:
                  if self.last_selected_image_path: similar_image_path = self.last_selected_image_path; print(f"  Using last selected image for similarity: {similar_image_path}")
                  else: QMessageBox.warning(self, "Similarity Search", "Please select an image first."); return
             sim_index = self.sorting_combo.findText("Similarity")
             if sim_index != -1:
                  self.suppress_search_on_dropdown_update = True
                  self.sorting_combo.model().item(sim_index).setEnabled(True)
                  if self.sorting_combo.currentIndex() != sim_index: self.sorting_combo.setCurrentIndex(sim_index)
                  self.sort_order_combo.setEnabled(True)
                  self.suppress_search_on_dropdown_update = False
                  self.similarity_mode = True
        elif current_sort_is_similarity:
             if self.last_selected_image_path: similarity_search, similar_image_path = True, self.last_selected_image_path; print(f"  Using last selected image due to Similarity sort: {similar_image_path}")
             else:
                  print("  Warning: Similarity sort selected, but no image context. Switching to 'Date'.")
                  self.suppress_search_on_dropdown_update = True; self.sorting_combo.setCurrentText("Date"); self.sort_order_combo.setEnabled(True); self.suppress_search_on_dropdown_update = False; self.similarity_mode = False

        try:
            image_paths: List[str] = []
            if not self.active_directories: print("  No active directories. Clearing gallery.")
            elif similarity_search and similar_image_path: image_paths = self._perform_similarity_search(search_query, similar_image_path, tags)
            elif search_query: image_paths = self._perform_normal_search(search_query)
            else: image_paths = self._get_all_images_from_active_directories()

            image_paths = self._filter_images_by_existence(image_paths)
            image_paths = self._sort_images(image_paths, similarity_search)
            self._update_gallery_display(image_paths)
            print(f"ImageGallery: Search completed. Displaying {len(image_paths)} images.")
        except ValueError as e: print(f"Syntax error: {e}"); QMessageBox.warning(self, "Syntax Error", f"Invalid search query: {str(e)}")
        except Exception as e: print(f"Unexpected search error: {e}"); traceback.print_exc(); QMessageBox.critical(self, "Search Error", f"An unexpected error occurred:\n{str(e)}")

    def _get_all_images_from_active_directories(self) -> List[str]:
        print("ImageGallery: _get_all_images_from_active_directories")
        if not self.active_directories: return []
        image_paths = []
        try:
            with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor(); dir_conditions = []; params = []
                for directory in self.active_directories:
                    norm_dir = self.db.normalize_path(directory);
                    if not norm_dir.endswith('/'): norm_dir += '/'
                    dir_conditions.append("path LIKE ?"); params.append(f"{norm_dir}%")
                if not dir_conditions: return []
                where_clause = " OR ".join(dir_conditions)
                query = f"SELECT path FROM images WHERE {where_clause}"
                cursor.execute(query, params); image_paths = [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e: print(f"DB Error getting all images: {e}"); return []
        print(f"  Returning {len(image_paths)} paths from active directories.")
        return image_paths

    def _perform_normal_search(self, search_query: str) -> List[str]:
        print(f"ImageGallery: _perform_normal_search query: '{search_query}'")
        parser = SearchQueryParser(); ast = parser.parse(search_query)
        evaluator = SearchQueryEvaluator(self.db, self.active_directories)
        image_ids = evaluator.evaluate(ast)
        image_paths = self._get_image_paths_from_ids(image_ids)
        print(f"  Normal search found {len(image_paths)} paths.")
        return image_paths

    def _perform_similarity_search(self, search_query: str, similar_image_path: str, tags: Optional[List[TagPrediction]]) -> List[str]:
        print(f"ImageGallery: _perform_similarity_search for: '{similar_image_path}'")
        base_image_paths: List[str] = self._perform_normal_search(search_query) if search_query else self._get_all_images_from_active_directories()
        print(f"  Base set for similarity: {len(base_image_paths)} images.")
        if not base_image_paths: return []

        reference_tags: Set[str]
        if tags: reference_tags = {tag.tag for tag in tags}; print(f"  Using {len(reference_tags)} temporary tags.")
        else:
            _, db_tags = self.db.get_image_info_by_path(similar_image_path)
            if db_tags is None: print(f"  Warning: Could not get tags for ref image {similar_image_path}"); return []
            reference_tags = {tag.tag for tag in db_tags}; print(f"  Using {len(reference_tags)} DB tags.")
        if not reference_tags: print("  Ref image has no tags."); return []

        image_similarity: List[Tuple[str, float]] = []
        try:
            with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                paths_to_query = [p for p in base_image_paths if self.db.normalize_path(p) != self.db.normalize_path(similar_image_path)]
                if not paths_to_query: return [] # Only the reference image matched base query

                path_placeholders = ','.join('?' for _ in paths_to_query)
                cursor.execute(f"""
                    SELECT i.path, t.name FROM images i
                    LEFT JOIN image_tags it ON i.id = it.image_id
                    LEFT JOIN tags t ON it.tag_id = t.id
                    WHERE i.path IN ({path_placeholders})
                """, paths_to_query)
                path_tags_map = defaultdict(set)
                for path, tag_name in cursor.fetchall():
                     if tag_name: path_tags_map[path].add(tag_name)

                for path in paths_to_query: # Iterate through original list to maintain order if needed
                    current_tags = path_tags_map.get(path, set())
                    intersection = len(reference_tags.intersection(current_tags))
                    union = len(reference_tags.union(current_tags))
                    similarity = intersection / union if union > 0 else 0.0
                    image_similarity.append((path, similarity))
        except sqlite3.Error as e: print(f"DB Error during similarity calc: {e}"); return []

        image_similarity.sort(key=lambda x: x[1], reverse=True)
        sorted_paths = [path for path, score in image_similarity]
        print(f"  Similarity search returning {len(sorted_paths)} paths.")
        return sorted_paths

    def _get_image_paths_from_ids(self, image_ids: Set[str]) -> List[str]:
        print(f"ImageGallery: _get_image_paths_from_ids for {len(image_ids)} IDs")
        if not image_ids: return []
        image_paths = []
        try:
            with self.db.lock, sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor(); id_placeholders = ','.join('?' for _ in image_ids)
                query = f"SELECT path FROM images WHERE id IN ({id_placeholders})"
                cursor.execute(query, list(image_ids)); image_paths = [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e: print(f"DB Error getting paths from IDs: {e}"); return []
        print(f"  Found {len(image_paths)} paths for IDs.")
        return image_paths # Note: This doesn't re-apply active_dir filter, assuming evaluator did

    def _filter_images_by_existence(self, image_paths: List[str]) -> List[str]:
        print(f"ImageGallery: _filter_images_by_existence (checking {len(image_paths)} paths)")
        valid_paths = [path for path in image_paths if Path(path).is_file()]
        removed_count = len(image_paths) - len(valid_paths)
        if removed_count > 0: print(f"  Removed {removed_count} non-existent paths.")
        return valid_paths

    def _sort_images(self, image_paths: List[str], is_similarity_search: bool) -> List[str]:
        print(f"ImageGallery: _sort_images ({len(image_paths)} paths), similarity={is_similarity_search}")
        if is_similarity_search: print("  Skipping sort for similarity results."); return image_paths

        sort_by = self.sorting_combo.currentText()
        sort_order_desc = (self.sort_order_combo.currentText() == '↓ Desc')

        if sort_by == 'Random': print("  Sorting: Random"); random.shuffle(image_paths)
        else:
            print(f"  Sorting by: {sort_by}, Order: {'Desc' if sort_order_desc else 'Asc'}")
            sort_key_func: Optional[Callable[[str], Any]] = None
            try:
                if sort_by == 'Date': sort_key_func = lambda p: Path(p).stat().st_mtime
                elif sort_by == 'File Size': sort_key_func = lambda p: Path(p).stat().st_size
                elif sort_by == 'Resolution':
                    # Use pre-fetched resolution data instead of opening files
                    def get_pixels_from_cache(p: str) -> int:
                        res_str = self.image_resolutions.get(p)
                        if res_str and 'x' in res_str:
                            try:
                                width_str, height_str = res_str.split('x', 1)
                                return int(width_str) * int(height_str)
                            except (ValueError, TypeError):
                                return 0 # Default for invalid format
                        return 0 # Default if resolution not found
                    sort_key_func = get_pixels_from_cache
                elif sort_by == 'Aspect Ratio': sort_key_func = self.get_image_aspect_ratio
                # Add safety checks for file existence within lambda if needed
            except Exception as e: print(f"Error defining sort key for {sort_by}: {e}")

            if sort_key_func:
                try:
                    decorated = [(sort_key_func(path), path) for path in image_paths if Path(path).is_file()] # Check existence here
                    decorated.sort(key=lambda x: x[0] if x[0] is not None else (float('-inf') if sort_order_desc else float('inf')), reverse=sort_order_desc)
                    image_paths = [path for key, path in decorated]
                except Exception as e: print(f"Error during sorting by {sort_by}: {e}")
            else: print(f"  Warning: Unknown sort key '{sort_by}'. Sorting by path."); image_paths.sort(reverse=sort_order_desc)
        return image_paths

    def _update_gallery_display(self, image_paths: List[str]):
        print(f"ImageGallery: _update_gallery_display with {len(image_paths)} images")
        self.all_images = image_paths # Store the list of paths to display

        # Fetch resolution strings for these paths from the database
        print("  Fetching resolutions from DB...")
        self.image_resolutions = self.db.get_resolutions_for_paths(image_paths)
        print(f"  Fetched {len(self.image_resolutions)} resolutions.")

        # Clear and repopulate the aspect ratio cache using the fetched resolutions
        self.aspect_ratios.clear()
        print("  Calculating aspect ratios from resolutions...")
        missing_res_count = 0
        for img_path in self.all_images:
            # get_image_aspect_ratio will now use self.image_resolutions
            ratio = self.get_image_aspect_ratio(img_path)
            if ratio is None:
                missing_res_count += 1
                ratio = 1.0 # Default aspect ratio if resolution is missing/invalid
            self.aspect_ratios[img_path] = ratio
        if missing_res_count > 0:
             print(f"  Warning: Could not determine aspect ratio for {missing_res_count} images (missing/invalid resolution in DB?). Used default 1.0.")
        print("  Aspect ratios updated.")
        self.total_pages = max(1, math.ceil(len(self.all_images) / self.page_size))
        self.total_pages_label.setText(f"of {self.total_pages}")
        self.page_number_edit.setMaximum(self.total_pages)
        self.current_page = 1
        self.page_number_edit.setValue(self.current_page)
        self.arrange_rows()

    # --- Helper Methods ---
    def get_image_pixels(self, path: str) -> int:
        try:
            with Image.open(path) as img: return img.width * img.height
        except Exception: return 0

    def get_image_aspect_ratio(self, path: str) -> Optional[float]:
        # 1. Check memory cache first
        if path in self.aspect_ratios:
            return self.aspect_ratios[path]

        # 2. Check pre-loaded resolution string cache
        resolution_str = self.image_resolutions.get(path)
        if resolution_str and 'x' in resolution_str:
            try:
                width_str, height_str = resolution_str.split('x', 1)
                width = int(width_str)
                height = int(height_str)
                if height > 0:
                    ratio = width / height
                    self.aspect_ratios[path] = ratio # Cache the calculated ratio
                    return ratio
                else:
                    # Handle zero height case
                    self.aspect_ratios[path] = 1.0 # Cache default
                    return 1.0
            except (ValueError, TypeError) as e:
                # Handle parsing errors (invalid format in DB?)
                print(f"Warning: Could not parse resolution string '{resolution_str}' for {path}: {e}")
                self.aspect_ratios[path] = 1.0 # Cache default
                return 1.0 # Return default ratio on error
        
        # 3. If resolution string not found or invalid, return None (or default 1.0?)
        # Let's return None to indicate failure, the calling code handles default.
        # print(f"Warning: No valid resolution string found for {path} in self.image_resolutions.")
        return None

    def unload_model_safely(self):
        print("Unloading model..."); self.model.unload_model(); print("Model unloaded.")

    def set_ui_enabled(self, enabled: bool, during_slideshow: bool = False):
        """
        Enables/disables UI elements during long operations or slideshow.
        If during_slideshow is True, 'enabled' typically means False for most controls.
        """
        # Controls always affected by long processing (like directory scan)
        self.advanced_search_panel.search_button.setEnabled(enabled)
        self.manage_directories_button.setEnabled(enabled)

        # Controls affected by both processing AND slideshow state
        slideshow_running = self.is_slideshow_active

        # Search field: Disabled during processing OR slideshow
        self.advanced_search_panel.search_field.setEnabled(enabled and not slideshow_running)

        # Gallery controls: Disabled during processing OR slideshow
        self.slider.setEnabled(enabled and not slideshow_running)
        self.page_number_edit.setEnabled(enabled and not slideshow_running)
        self.page_size_edit.setEnabled(enabled and not slideshow_running)
        self.prev_button.setEnabled(enabled and not slideshow_running)
        self.next_button.setEnabled(enabled and not slideshow_running)
        self.sorting_combo.setEnabled(enabled and not slideshow_running)
        # Only enable sort order if not random AND not slideshow
        is_random_sort = self.sorting_combo.currentText() == "Random"
        self.sort_order_combo.setEnabled(enabled and not is_random_sort and not slideshow_running)

        # Slideshow controls: Enabled ONLY if not processing, state depends on slideshow itself
        if self.slideshow_button:
            self.slideshow_button.setEnabled(enabled) # Button itself is enabled unless processing
        if self.slideshow_delay_spinbox:
            self.slideshow_delay_spinbox.setEnabled(enabled and not slideshow_running) # Delay editable only when stopped & not processing

    # --- Slideshow Methods ---
    @pyqtSlot()
    def toggle_slideshow(self):
        """Starts or stops the image slideshow."""
        if self.is_slideshow_active:
            self.stop_slideshow()
        else:
            self.start_slideshow()

    def start_slideshow(self):
        """Initializes and starts the slideshow."""
        if not self.all_images:
            QMessageBox.information(self, "Slideshow", "No images found matching the current criteria.")
            return

        if self.is_slideshow_active:
            return # Already running

        print("Starting slideshow...")
        self.is_slideshow_active = True
        self.slideshow_images = list(self.all_images) # Take a copy
        self.slideshow_current_index = -1 # Will be incremented before first display

        if self.slideshow_button:
            self.slideshow_button.setText("⏹ Stop Slideshow")

        # Disable interfering UI elements
        self.set_ui_enabled(True, during_slideshow=True) # Pass True to indicate slideshow context

        # Start the cycle
        self.advance_slideshow()

    @pyqtSlot() # Allow connection from signals like search/sort changes
    def stop_slideshow(self):
        """Stops the currently running slideshow."""
        if not self.is_slideshow_active:
            return

        print("Stopping slideshow...")
        self.is_slideshow_active = False
        self.slideshow_timer.stop()
        self.slideshow_images = []
        self.slideshow_current_index = -1

        if self.slideshow_button:
            self.slideshow_button.setText("▶ Start Slideshow")

        # Re-enable UI elements
        self.set_ui_enabled(True, during_slideshow=False) # Pass False to indicate slideshow stopped

    @pyqtSlot() # Connected to timer timeout
    def advance_slideshow(self):
        """Advances to the next image in the slideshow."""
        if not self.is_slideshow_active or not self.slideshow_images:
            self.stop_slideshow() # Stop if state is inconsistent
            return

        self.slideshow_current_index += 1
        if self.slideshow_current_index >= len(self.slideshow_images):
            self.slideshow_current_index = 0 # Loop back

        next_image_path = self.slideshow_images[self.slideshow_current_index]
        print(f"Slideshow advancing to index {self.slideshow_current_index}: {next_image_path}")

        # Display image - the timer restart logic is now in handle_load_result
        self.display_image_in_preview(next_image_path)

    # --- End Slideshow Methods ---

    # --- Cleanup ---
    def closeEvent(self, event):
        self.stop_slideshow() # Ensure slideshow stops cleanly
        self.unload_model_safely()
        # Wait for threadpool to finish?
        # self.threadpool.waitForDone()
