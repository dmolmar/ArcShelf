from typing import TYPE_CHECKING, Optional, List

from PyQt6.QtWidgets import QLabel, QMenu, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QAction, QPixmap, QResizeEvent

# Use TYPE_CHECKING for type hints to avoid circular imports
if TYPE_CHECKING:
    from gui.main_window import ImageGallery
    from database.models import TagPrediction # For temporary_predictions hint

# Define supported image file extensions
SUPPORTED_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff')

class DragDropArea(QLabel):
    """
    A QLabel widget that accepts dragged-and-dropped image files.
    Displays the dropped image and provides context menu actions.
    Interacts with the main ImageGallery instance.
    """
    # Signal emitted when an image is successfully dropped and processed (optional)
    # image_selected = pyqtSignal(str) # Path of the dropped image

    def __init__(self, image_gallery_instance: 'ImageGallery'):
        """
        Initializes the DragDropArea.

        Args:
            image_gallery_instance: A reference to the main ImageGallery instance.
        """
        super().__init__()
        self.image_gallery = image_gallery_instance
        self.dropped_image_path: Optional[str] = None
        # Store temporary predictions for similarity search of non-db images
        self.temporary_predictions: Optional[List['TagPrediction']] = None

        self._original_pixmap: Optional[QPixmap] = None
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._perform_rescale)
        self._debounce_ms = 50 # Adjust debounce time as needed (milliseconds)

        self.setAcceptDrops(True) # Enable drop events
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText("Drag and drop an image here\n(for preview and similarity search)")
        self.setWordWrap(True)
        # Basic styling
        self.setStyleSheet("""
            QLabel {
                border: 2px dashed #aaa;
                border-radius: 5px;
                color: #888;
                padding: 0px;
                min-height: 100px; /* Ensure it has some minimum size */
            }
        """)
        # Set size policy to allow expansion
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    
    def resizeEvent(self, event: QResizeEvent):
        """Handle widget resize events to trigger pixmap rescaling (debounced)."""
        super().resizeEvent(event)
        # If we have an original pixmap stored, start the debounce timer
        # to trigger a rescale calculation after the resizing stops.
        if self._original_pixmap and not self._original_pixmap.isNull():
            self._resize_timer.start(self._debounce_ms)
    
    def minimumSizeHint(self) -> QSize:
        """Override minimum size hint to allow shrinking horizontally."""
        # Return a very small size (e.g., 10x10 pixels or even 0x0)
        # This tells the layout/splitter that the widget can become very narrow.
        # The actual content scaling is handled by resizeEvent/_perform_rescale.
        return QSize(10, 10)
    
    def _perform_rescale(self):
        """Scales the stored original pixmap to the current label size and sets it."""
        if not self._original_pixmap or self._original_pixmap.isNull():
            return # No original pixmap to rescale

        # Get current size and calculate available area inside border
        border_width = 2
        width_offset = border_width * 2
        height_offset = border_width * 2
        label_size = self.size()
        available_width = max(1, label_size.width() - width_offset)
        available_height = max(1, label_size.height() - height_offset)
        available_size = QSize(available_width, available_height)

        if available_size.width() <= 0 or available_size.height() <= 0:
            print("Warning: Cannot rescale pixmap, available size is invalid.")
            return # Don't attempt to scale if size is invalid

        # Scale the *original* pixmap
        scaled_pixmap = self._original_pixmap.scaled(
            available_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        # Set the newly scaled pixmap
        self.setPixmap(scaled_pixmap)

    def dragEnterEvent(self, event: QDragEnterEvent):
        """Accepts drag events if they contain URLs (potential files)."""
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            # Check if any URL is a supported image file
            for url in mime_data.urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if path.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS):
                        event.acceptProposedAction()
                        return
            # If no supported image found
            event.ignore()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        """Handles the dropping of a file."""
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS):
                print(f"DragDropArea: Dropped image path: {path}")
                self.dropped_image_path = path
                self.temporary_predictions = None # Clear previous temp predictions
                self._original_pixmap = None

                # Display image preview
                if hasattr(self.image_gallery, 'display_image_in_preview'):
                    self.image_gallery.display_image_in_preview(path, target_label=self)

                # Trigger analysis (which will call set_temporary_predictions upon completion)
                if hasattr(self.image_gallery, 'process_image_info'):
                    # --- CHANGE: Pass the image path ---
                    self.image_gallery.process_image_info(path, analyze=True, store_temp_predictions_callback=self.set_temporary_predictions)
                    # --- END CHANGE ---

                event.acceptProposedAction()
            else:
                print(f"DragDropArea: Dropped file is not a supported image: {path}")
                event.ignore()
        else:
            event.ignore()

    def set_temporary_predictions(self, predictions: Optional[List['TagPrediction']]):
        """Callback function to receive temporary predictions from ImageGallery."""
        print(f"DragDropArea: Received temporary predictions ({len(predictions) if predictions else 0} tags)")
        self.temporary_predictions = predictions

        # --- NEW FEATURE: Trigger automatic similarity search ---
        if predictions is not None and self.dropped_image_path:
            print(f"DragDropArea: Automatically triggering similarity search for {self.dropped_image_path}")
            # Ensure the image gallery reference is valid
            if hasattr(self.image_gallery, 'perform_search'):
                 self.image_gallery.perform_search(
                    similarity_search=True,
                    similar_image_path=self.dropped_image_path,
                    tags=self.temporary_predictions # Use the newly predicted tags
                )
            else:
                 print("DragDropArea: Error - ImageGallery reference invalid or missing 'perform_search'.")
        elif self.dropped_image_path:
             # Analysis might have failed if predictions is None
             print("DragDropArea: Analysis failed for dropped image, cannot trigger auto-search.")

        # --- CHANGE: Clear the callback reference in the main window ---
        # This assumes _temp_pred_callback is accessible or we have another way
        # For simplicity, let's assume ImageGallery clears it after calling.
        # If not, add: self.image_gallery._temp_pred_callback = None
        # --- END CHANGE ---

    def contextMenuEvent(self, event):
        """Shows context menu only if an image is currently displayed."""
        current_pixmap = self.pixmap()
        if current_pixmap and not current_pixmap.isNull():
            context_menu = QMenu(self)

            search_similar_action = QAction("Search Similar Images", self)
            search_similar_action.triggered.connect(self.search_similar_images)
            # Enable only if an image path is known
            search_similar_action.setEnabled(bool(self.dropped_image_path or self.image_gallery.last_selected_image_path))
            context_menu.addAction(search_similar_action)

            remove_image_action = QAction("Remove Image from Preview", self)
            remove_image_action.triggered.connect(self.remove_image)
            context_menu.addAction(remove_image_action)

            context_menu.exec(event.globalPos())

    def search_similar_images(self):
        """Initiates a similarity search based on the currently displayed image."""
        print(f"DragDropArea: search_similar_images called.")

        # Prioritize the image dropped onto this area
        if self.dropped_image_path:
            print(f"DragDropArea: Searching similar to dropped image: {self.dropped_image_path}")
            # Use temporary predictions if available for the dropped image
            tags_to_use = self.temporary_predictions
            if tags_to_use:
                 print(f"DragDropArea: Using {len(tags_to_use)} temporary predictions for similarity search.")
            else:
                 print("DragDropArea: No temporary predictions available for dropped image.")
                 # Optionally, could try to fetch from DB if it happens to be there, but less likely
                 # tags_to_use = self.image_gallery.get_tags_for_path_from_db(self.dropped_image_path)

            self.image_gallery.perform_search(
                similarity_search=True,
                similar_image_path=self.dropped_image_path,
                tags=tags_to_use # Pass temporary tags
            )
        # Fallback to the last image selected in the main gallery if nothing was dropped here
        elif self.image_gallery.last_selected_image_path:
            print(f"DragDropArea: No dropped image, searching similar to last selected gallery image: {self.image_gallery.last_selected_image_path}")
            # For gallery images, tags should come from the database via perform_search
            self.image_gallery.perform_search(
                similarity_search=True,
                similar_image_path=self.image_gallery.last_selected_image_path
                # Tags will be fetched inside perform_search for DB images
            )
        else:
            print("DragDropArea: No image available (dropped or selected) for similarity search.")
            # Optionally show a status message to the user

    def remove_image(self):
        """Clears the displayed image and associated data."""
        print("DragDropArea: Remove Image clicked")
        self.clear() # Clears the pixmap
        self.setText("Drag and drop an image here\n(for preview and similarity search)")
        self.dropped_image_path = None
        self.temporary_predictions = None
        self._original_pixmap = None
        # Optionally clear the gallery's last selected path if it matches the removed one?
        if self.image_gallery.last_selected_image_path == self.dropped_image_path:
            self.image_gallery.last_selected_image_path = None
        print(f"DragDropArea: Preview cleared.")