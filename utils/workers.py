import sys
import traceback
from typing import TYPE_CHECKING, Any, Callable, Optional

from PyQt6.QtCore import QObject, QRunnable, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage, QPixmap
from PIL import UnidentifiedImageError
# from PIL import Image # Removed unused PIL import

# Use TYPE_CHECKING to avoid circular import for ThumbnailCache type hint
if TYPE_CHECKING:
    from image_processing.thumbnail import ThumbnailCache

# --- Generic Worker ---

class WorkerSignals(QObject):
    """
    Defines signals available from a running Worker thread.

    Supported signals:
    - finished: Emitted when the task completes successfully. Passes the return value.
    - error: Emitted when the task fails with an exception. Passes (exception, traceback_str).
    - result: Emitted to send intermediate results (object).
    - progress: Emitted to update progress (int).
    - update_info_text: Emitted to update status text (str).
    """
    finished = pyqtSignal(object)
    error = pyqtSignal(tuple)
    result = pyqtSignal(object)
    progress = pyqtSignal(int)
    update_info_text = pyqtSignal(str) # Example signal for status updates

class Worker(QRunnable):
    """
    Generic worker thread that runs a function with given args and kwargs.
    """
    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any):
        """
        Initializes the worker.

        Args:
            fn: The function to run in the background.
            *args: Positional arguments to pass to the function.
            **kwargs: Keyword arguments to pass to the function.
                     A special 'directory' kwarg was handled previously,
                     ensure the calling code passes it if needed by 'fn'.
        """
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

        # Optional: Add callback hook for progress reporting within fn
        # self.kwargs['progress_callback'] = self.signals.progress.emit
        # self.kwargs['status_callback'] = self.signals.update_info_text.emit

    @pyqtSlot()
    def run(self):
        """Execute the worker's function and emit signals."""
        try:
            # Execute the function with stored arguments
            result = self.fn(*self.args, **self.kwargs)
            # Emit the final result via the finished signal
            self.signals.finished.emit(result)
        except Exception as e:
            print(f"Worker error in function '{self.fn.__name__}': {e}", file=sys.stderr)
            traceback_str = traceback.format_exc()
            print(traceback_str, file=sys.stderr)
            # Emit the error signal with exception and traceback
            self.signals.error.emit((e, traceback_str))

# --- Thumbnail Loader Worker ---

class ThumbnailLoaderSignals(QObject):
    """Defines signals for the ThumbnailLoader."""
    # Emit image_id (str) and the loaded/scaled QPixmap
    thumbnailLoaded = pyqtSignal(str, QPixmap)
    # Optional: Signal for loading errors specific to this thumbnail
    thumbnailError = pyqtSignal(str, str) # image_id, error_message

class ThumbnailLoader(QRunnable):
    """
    Worker thread specifically for loading and scaling thumbnails.
    Uses ThumbnailCache for efficient loading.
    """
    def __init__(self, image_id: str, image_path: str, target_width: int, target_height: int, thumbnail_cache: 'ThumbnailCache'):
        """
        Initializes the thumbnail loader.

        Args:
            image_id: The unique ID of the image.
            image_path: The full path to the original image file.
            target_width: The target width for the displayed thumbnail.
            target_height: The target height for the displayed thumbnail.
            thumbnail_cache: The ThumbnailCache instance.
        """
        super().__init__()
        self.image_id = image_id
        self.image_path = image_path
        self.target_width = target_width
        self.target_height = target_height
        self.thumbnail_cache = thumbnail_cache
        self.signals = ThumbnailLoaderSignals()

    @pyqtSlot()
    def run(self):
        """Load thumbnail from cache or generate, then emit."""
        try:
            # 1. Try getting QImage from cache
            qimage: Optional[QImage] = self.thumbnail_cache.get_thumbnail(self.image_id)

            # 2. If not in cache, try generating it (update_thumbnail handles saving)
            if qimage is None:
                print(f"Thumbnail cache miss for {self.image_id}, generating...")
                # update_thumbnail handles opening, resizing, saving to disk cache
                self.thumbnail_cache.update_thumbnail(self.image_path, self.image_id)
                # After update, try getting it again (it should be in memory cache now)
                qimage = self.thumbnail_cache.get_thumbnail(self.image_id)

            # 3. If QImage is available (from cache or generated), create and scale Pixmap
            if qimage and not qimage.isNull():
                pixmap = QPixmap.fromImage(qimage)
                # Scale the pixmap smoothly to fit the target dimensions while keeping aspect ratio
                scaled_pixmap = pixmap.scaled(
                    self.target_width,
                    self.target_height,
                    Qt.AspectRatioMode.KeepAspectRatio, # Keep aspect ratio
                    Qt.TransformationMode.SmoothTransformation # Use smooth scaling
                )
                # Emit the final scaled pixmap
                self.signals.thumbnailLoaded.emit(self.image_id, scaled_pixmap)
            else:
                # Handle case where thumbnail couldn't be loaded or generated
                error_msg = f"Could not load or generate thumbnail for {self.image_id}"
                print(error_msg, file=sys.stderr)
                self.signals.thumbnailError.emit(self.image_id, error_msg)

        except FileNotFoundError:
             error_msg = f"Original image file not found: {self.image_path}"
             print(error_msg, file=sys.stderr)
             self.signals.thumbnailError.emit(self.image_id, error_msg)
        except UnidentifiedImageError:
             error_msg = f"Cannot identify image file (corrupt/unsupported): {self.image_path}"
             print(error_msg, file=sys.stderr)
             self.signals.thumbnailError.emit(self.image_id, error_msg)
        except Exception as e:
            error_msg = f"Error loading thumbnail for {self.image_id}: {e}"
            print(error_msg, file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            self.signals.thumbnailError.emit(self.image_id, error_msg)