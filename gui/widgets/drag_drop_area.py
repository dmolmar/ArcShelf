# gui/widgets/drag_drop_area.py

import sys
import os
import subprocess
import math
import datetime
import requests # Added requests
from pathlib import Path
from typing import TYPE_CHECKING, Optional, List, Tuple

from PyQt6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsTextItem,
    QMenu, QApplication, QSizePolicy, QFrame, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPointF, QRectF, QSize, QUrl, QMimeData
from PyQt6.QtGui import (
    QDragEnterEvent, QDropEvent, QAction, QPixmap, QResizeEvent, QWheelEvent,
    QMouseEvent, QPainter, QColor, QDragMoveEvent, QKeyEvent, QKeySequence, QImage,
    QDrag  # Added for drag-to-external
)

import config # Import config for TEMP_DIR

# Use TYPE_CHECKING for type hints to avoid circular imports
if TYPE_CHECKING:
    from gui.main_window import ImageGallery
    from database.models import TagPrediction # For temporary_predictions hint

# Define supported image file extensions
SUPPORTED_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff')

# --- Constants ---
ZOOM_FACTOR = 1.15
MAX_ZOOM_LEVEL = 15.0
FIT_SCALE_TOLERANCE = 0.001
TARGET_PIXEL_DENSITY_RATIO = 1.2
MIN_LOD_STEP_FACTOR = 1.7
# Minimum dimension for *generated* LODs (safety net)
MINIMUM_LOD_GENERATION_DIM = 32

class DragDropArea(QGraphicsView):
    # ... (other methods like __init__, set_image, placeholders etc. are mostly the same) ...

    def __init__(self, image_gallery_instance: 'ImageGallery'):
        super().__init__()
        self.image_gallery = image_gallery_instance
        self.dropped_image_path: Optional[str] = None
        self.temporary_predictions: Optional[List['TagPrediction']] = None

        self._scene = QGraphicsScene(self)
        self._pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._placeholder_text_item: Optional[QGraphicsTextItem] = None
        self.setScene(self._scene)

        self._full_res_pixmap: Optional[QPixmap] = None
        self._lods: List[Tuple[int, QPixmap]] = []
        self._is_panning: bool = False
        self._last_pan_point: QPointF = QPointF()
        self._drag_start_pos: Optional[QPointF] = None  # For drag-to-external detection
        self._current_view_scale: float = 1.0
        self._fit_scale_full_res: float = 1.0

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        # Connect timer to a new method that handles both regeneration and fitting
        self._resize_timer.timeout.connect(self._regenerate_lods_and_fit)
        self._debounce_ms = 150 # Increase debounce slightly for resize

        # --- View Configuration ---
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(100, 100)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus) # Enable focus for key events

        self._show_placeholder_text()

    # --- set_image, _clear_scene_items, _show_placeholder_text, _center_placeholder ---
    # (These remain unchanged from the previous version)
    def set_image(self, pixmap: Optional[QPixmap], is_placeholder: bool = False):
        """Loads the image, optionally generates LODs, and sets the initial view.
        
        Args:
            pixmap: The image to display, or None to show placeholder text.
            is_placeholder: If True, skip LOD generation for fast display (used for
                          thumbnail placeholders that will be replaced by full-res).
        """
        self._clear_scene_items()
        self._full_res_pixmap = None
        self._lods = []

        if pixmap and not pixmap.isNull():
            self._full_res_pixmap = pixmap
            
            # Unified path: Both placeholder and full-res use identical code
            # Placeholder just skips expensive LOD generation
            self._pixmap_item = QGraphicsPixmapItem()
            self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self._scene.addItem(self._pixmap_item)
            
            self._scene.setSceneRect(QRectF(self._full_res_pixmap.rect()))
            
            if is_placeholder:
                # Single LOD = the thumbnail itself (skip LOD generation for speed)
                self._lods = [(self._full_res_pixmap.width(), self._full_res_pixmap)]
            else:
                # Generate multiple LODs for full-res
                if self.viewport() and self.viewport().size().width() > 0:
                    self._generate_lods()
                else:
                    self._lods = [(self._full_res_pixmap.width(), self._full_res_pixmap)]
            
            self.fit_image_in_view()

        else:
            self._show_placeholder_text()
            self._scene.setSceneRect(QRectF(self.viewport().rect()))

    def _clear_scene_items(self):
        """Removes image and placeholder items from the scene."""
        if self._pixmap_item and self._pixmap_item.scene() == self._scene:
            self._scene.removeItem(self._pixmap_item)
        if self._placeholder_text_item and self._placeholder_text_item.scene() == self._scene:
            self._scene.removeItem(self._placeholder_text_item)
        self._pixmap_item = None
        self._placeholder_text_item = None

    def _show_placeholder_text(self):
        """Adds or updates the placeholder text item."""
        self._clear_scene_items()
        # Reset image data
        self._full_res_pixmap = None
        self._lods = []

        placeholder_text = "Drag and drop an image here\n(for preview and similarity search)"
        self._placeholder_text_item = QGraphicsTextItem(placeholder_text)
        font = self.font()
        font.setPointSize(font.pointSize() + 2)
        self._placeholder_text_item.setFont(font)
        self._placeholder_text_item.setDefaultTextColor(QColor("#888"))
        self._scene.addItem(self._placeholder_text_item)
        self._center_placeholder()
        self._scene.setSceneRect(QRectF(self.viewport().rect())) # Fit scene to view
        self.resetTransform() # Reset any zoom/pan
        self._current_view_scale = 1.0
        self._fit_scale_full_res = 1.0 # Reset fit scale

    def _center_placeholder(self):
        """Helper to center the placeholder text."""
        if not self._placeholder_text_item or not self.viewport(): return
        text_rect = self._placeholder_text_item.boundingRect()
        view_rect = self.viewport().rect()
        center_x = max(0.0, (view_rect.width() - text_rect.width()) / 2.0)
        center_y = max(0.0, (view_rect.height() - text_rect.height()) / 2.0)
        # Adjust for scene coordinates if view isn't at 0,0 (though it should be for placeholder)
        scene_origin = self.mapToScene(0, 0)
        self._placeholder_text_item.setPos(scene_origin.x() + center_x, scene_origin.y() + center_y)


    # ==============================================================
    # REVISED LOD GENERATION LOGIC
    # ==============================================================
    def _generate_lods(self):
        """Generates multiple LOD pixmaps based on view size and constants."""
        if not self._full_res_pixmap or self._full_res_pixmap.isNull():
            self._lods = []
            return

        view = self.viewport()
        if not view or view.width() <= 0 or view.height() <= 0:
            print("DragDropArea: Warning - Cannot generate LODs with invalid viewport.")
            if self._full_res_pixmap:
                 self._lods = [(self._full_res_pixmap.width(), self._full_res_pixmap)]
            else:
                 self._lods = []
            return

        view_size = view.size()
        full_res_width = self._full_res_pixmap.width()
        full_res_height = self._full_res_pixmap.height()

        if full_res_width <= 0 or full_res_height <= 0:
             print("DragDropArea: Warning - Cannot generate LODs for zero-sized source pixmap.")
             self._lods = []
             return

        # Calculate the scale factor when the full-res image *just fits* the view
        scale_x = float(view_size.width()) / full_res_width
        scale_y = float(view_size.height()) / full_res_height
        initial_fit_scale = min(scale_x, scale_y)
        if initial_fit_scale <= 0: initial_fit_scale = 1.0 # Safety

        # ================== KEY CHANGE HERE ==================
        # Calculate the target width for the LOWEST resolution LOD needed.
        # This depends on the full image width scaled down by the initial fit scale,
        # plus the density ratio requirement.
        min_target_width = float(full_res_width) * initial_fit_scale * TARGET_PIXEL_DENSITY_RATIO
        # ======================================================

        # Ensure min_target_width isn't excessively small or larger than full-res
        min_target_width = max(float(MINIMUM_LOD_GENERATION_DIM), min(min_target_width, float(full_res_width)))

        print(f"DragDropArea: Generating LODs. View: {view_size.width()}x{view_size.height()}, "
              f"Image: {full_res_width}x{full_res_height}, InitialFitScale: {initial_fit_scale:.4f}, "
              f"MinTargetWidth (LOD): {min_target_width:.1f}")

        # --- LOD Generation Loop (mostly same as before) ---
        new_lods = []
        new_lods.append((full_res_width, self._full_res_pixmap))

        current_lod_width = float(full_res_width)

        while True:
            next_lower_width = current_lod_width / MIN_LOD_STEP_FACTOR

            # Check if we need to generate the minimum target width explicitly
            generate_min_target = False
            if len(new_lods) == 1 and min_target_width < full_res_width / 1.1: # Only have full-res, need smaller
                 if next_lower_width < min_target_width: # Next step is already too small
                      generate_min_target = True

            # Stop conditions
            if not generate_min_target and (next_lower_width < min_target_width or next_lower_width < MINIMUM_LOD_GENERATION_DIM):
                 break

            if generate_min_target:
                 target_width_int = max(MINIMUM_LOD_GENERATION_DIM, int(round(min_target_width)))
                 # print(f"  Generating mandatory minimum target LOD near {target_width_int}w")
            else:
                 target_width_int = int(round(next_lower_width))

            if target_width_int < MINIMUM_LOD_GENERATION_DIM: break # Safety
            if target_width_int >= current_lod_width: break # Avoid scaling up or zero step

            scaled_pixmap = self._full_res_pixmap.scaledToWidth(
                target_width_int, Qt.TransformationMode.SmoothTransformation
            )

            if scaled_pixmap.isNull() or scaled_pixmap.width() <= 0:
                print(f"DragDropArea: Warning - Failed scale to width {target_width_int}.")
                break

            if abs(scaled_pixmap.width() - new_lods[-1][0]) > 1: # Check if meaningfully different
                 new_lods.append((scaled_pixmap.width(), scaled_pixmap))
                 current_lod_width = float(scaled_pixmap.width())
            else:
                 # print(f"  Skipping near-duplicate LOD generation at width {scaled_pixmap.width()}")
                 break # Stop if scale step was too small

            if len(new_lods) > 20: # Safety break
                print("DragDropArea: Warning - Generated too many LOD levels.")
                break

            if generate_min_target: # Only generate the specific min target once
                break


        new_lods.sort(key=lambda item: item[0], reverse=True)

        # Only replace if generation was successful
        if new_lods:
             # TODO: Consider if we need to handle QPixmap memory cleanup here if replacing large lists
             self._lods = new_lods
             print(f"DragDropArea: Generated {len(self._lods)} LOD levels. Widths: {[w for w, p in self._lods]}")
        else:
             print("DragDropArea: Error - Failed to generate any valid LODs.")
             if self._full_res_pixmap:
                 self._lods = [(self._full_res_pixmap.width(), self._full_res_pixmap)] # Fallback


    def fit_image_in_view(self):
        """Scales the view to fit the full-res image rect and updates LOD/item scale."""
        if not self._full_res_pixmap or not self._pixmap_item:
            if self._placeholder_text_item:
                 # Handle placeholder centering and scene reset
                 self._center_placeholder()
                 self._scene.setSceneRect(QRectF(self.viewport().rect()))
                 self.resetTransform()
                 self._current_view_scale = 1.0
                 self._fit_scale_full_res = 1.0
            return

        rect = self._scene.sceneRect()
        if rect.isNull() or not self.viewport() or rect.width() <= 0 or rect.height() <= 0:
             print("Warning: Cannot fit_image_in_view with invalid rect or viewport.")
             return

        # Fit the full-res scene rect into the view
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

        # Record the resulting view scale factor (pixels per scene unit)
        # Use m11, assuming KeepAspectRatio ensures m11 == m22
        self._current_view_scale = self.transform().m11()
        self._fit_scale_full_res = self._current_view_scale
        # print(f"Fit in view complete. New fit/current view scale: {self._current_view_scale:.4f}")

        # Select the appropriate LOD for this new scale
        self._update_display_pixmap_and_item_scale()

    # ==============================================================
    # REVISED LOD SELECTION LOGIC
    # ==============================================================
    def _update_display_pixmap_and_item_scale(self):
        """Selects the best LOD pixmap based on current view scale and sets item scale."""
        if not self._pixmap_item or not self._full_res_pixmap or not self._lods:
            return

        view = self.viewport()
        if not view or view.width() <= 0 or view.height() <= 0:
            return

        # ================== KEY CHANGE HERE ==================
        # Calculate the effective *image width* required by the view at the current scale
        # to satisfy the density ratio. This depends on the full image width and view scale.
        # (View scale = viewport pixels / scene units; scene units = full-res pixels)
        required_lod_width = (float(self._full_res_pixmap.width())
                              * self._current_view_scale
                              * TARGET_PIXEL_DENSITY_RATIO)
        # ======================================================

        # print(f"Debug: UpdateDisplayPixmap - ViewScale: {self._current_view_scale:.4f}, RequiredLODWidth: {required_lod_width:.1f}")

        # --- Select the best LOD (logic remains the same as previous fix) ---
        best_lod_width, best_lod_pixmap = self._lods[0] # Default/fallback to highest res

        for lod_width, lod_pixmap in self._lods:
            if lod_width >= required_lod_width:
                best_lod_width = lod_width
                best_lod_pixmap = lod_pixmap
            else:
                break # Found the smallest sufficient LOD
        
        # ==============================================================
        try:
            # Calculate the actual density ratio using the selected LOD
            if self._full_res_pixmap and self._current_view_scale > 1e-9: # Avoid division by zero/tiny scales
                full_res_width = float(self._full_res_pixmap.width())
                if full_res_width > 0 and best_lod_width > 0:
                    actual_density_ratio = best_lod_width / (full_res_width * self._current_view_scale)
                    print(f"  LOD Update: ViewScale={self._current_view_scale:.4f}, "
                          f"UsingLOD={best_lod_width}w, "
                          f"ActualDensityRatio={actual_density_ratio:.3f} "
                          f"(Target >= {TARGET_PIXEL_DENSITY_RATIO:.3f})") # Added target for comparison
                # else: Print nothing if widths are invalid
        except Exception as e:
            print(f"  Error calculating density ratio: {e}") # Catch unexpected errors
        # ==============================================================

        # --- Update QGraphicsPixmapItem (logic remains the same) ---
        current_item_pixmap = self._pixmap_item.pixmap()
        if current_item_pixmap is not best_lod_pixmap:
            # print(f"Switching LOD: RequiredW ~{required_lod_width:.0f} -> Using LOD {best_lod_width}x{best_lod_pixmap.height()}")
            self._pixmap_item.setPixmap(best_lod_pixmap)

        # --- Calculate and set item scale compensation (logic remains the same) ---
        full_res_width = self._full_res_pixmap.width()
        item_scale = 1.0
        if best_lod_width > 0 and full_res_width > 0:
             item_scale = float(full_res_width) / best_lod_width

        if abs(self._pixmap_item.scale() - item_scale) > FIT_SCALE_TOLERANCE:
            # print(f"  Updating Item Scale: {item_scale:.4f} (FullW: {full_res_width}, LodW: {best_lod_width})")
            self._pixmap_item.setScale(item_scale)


    def resizeEvent(self, event: QResizeEvent):
        """Handle widget resize events by triggering LOD regeneration and fitting."""
        super().resizeEvent(event)
        # Don't regenerate/fit if there's no image
        if not self._full_res_pixmap:
             # If placeholder exists, just recenter it
             if self._placeholder_text_item:
                  self._center_placeholder()
             return
        # Trigger the timer to regenerate LODs and fit view after resize settles
        self._resize_timer.start(self._debounce_ms)

    def _regenerate_lods_and_fit(self):
        """Slot called by resize timer to regenerate LODs and fit the view."""
        if not self._full_res_pixmap: # Check again in case image removed during debounce
            return
        print("Resize timer timeout: Regenerating LODs and fitting view.")
        self._generate_lods()   # Regenerate based on the *new* viewport size
        self.fit_image_in_view() # Fit the view, which also updates the displayed LOD


    # --- wheelEvent, mouse events, drag/drop, context menu, helpers ---
    # (These should be fine, relying on the corrected _update_display_pixmap_and_item_scale)
    def wheelEvent(self, event: QWheelEvent):
        """Handle mouse wheel events for zooming, update LOD and item scale."""
        if not self._full_res_pixmap or not self._lods:
            event.ignore()
            return

        current_view_scale = self.transform().m11() # Use m11 for horizontal scale factor
        delta = event.angleDelta().y()

        if delta > 0:
            factor = ZOOM_FACTOR
        elif delta < 0:
            factor = 1.0 / ZOOM_FACTOR
        else:
            event.ignore()
            return

        potential_new_scale = current_view_scale * factor

        # Apply Zoom Limits (based on view scale relative to full-res)
        # MAX_ZOOM_LEVEL is now an absolute view scale limit
        if potential_new_scale > MAX_ZOOM_LEVEL:
            factor = MAX_ZOOM_LEVEL / current_view_scale
            if factor <= 1.0 + FIT_SCALE_TOLERANCE: # Already at or above max zoom (with tolerance)
                 # print("Max zoom reached.")
                 event.accept(); return # Consume event, do nothing

        # Limit zooming out: Don't zoom out smaller than fitting the image
        # Check against the scale where the image fits the view
        if potential_new_scale < self._fit_scale_full_res - FIT_SCALE_TOLERANCE:
            # If we are *already* at the minimum scale (within tolerance),
            # accept the event but don't call fit_image_in_view again.
            if abs(current_view_scale - self._fit_scale_full_res) < FIT_SCALE_TOLERANCE:
                # print("Already at minimum zoom, ignoring further zoom out scroll.")
                event.accept()
                return

            # Otherwise, we are zooming out *towards* the minimum, so fit it exactly.
            # print("Zoom Out Limit: Fitting image to view.")
            self.fit_image_in_view() # Fit exactly (this calls update LOD)
            event.accept()
            return

        # Apply Scaling to the VIEW only if factor is significant
        if abs(factor - 1.0) > FIT_SCALE_TOLERANCE:
            # print(f"Zoom applied. Factor: {factor:.4f}, Old view scale: {current_view_scale:.4f}")
            self.scale(factor, factor)
            self._current_view_scale = self.transform().m11() # Update tracked VIEW scale *after* scaling
            # print(f"  New view scale: {self._current_view_scale:.4f}")

            # --- Update LOD and item scale ---
            self._update_display_pixmap_and_item_scale()
            event.accept()
        else:
            # Factor is too close to 1.0, ignore
            event.ignore()

    def mousePressEvent(self, event: QMouseEvent):
        """Handle mouse press for panning and drag-to-external."""
        if self._pixmap_item and event.button() == Qt.MouseButton.LeftButton:
            # Save position for potential drag-to-external
            self._drag_start_pos = event.position()
            
            # Allow panning if the view scale is noticeably larger than the fit scale
            # (meaning the image content is larger than the view)
            # Using a slightly larger tolerance might feel better
            if self._current_view_scale > self._fit_scale_full_res + (FIT_SCALE_TOLERANCE * 5):
                self._is_panning = True
                # Use viewport coordinates for panning calculations
                self._last_pan_point = event.position() # Use event.position() for QPointF
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
            else:
                 # Not zoomed in enough to pan, but we might drag to external
                 event.accept()
        else:
            self._drag_start_pos = None
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        """Handle mouse move events for panning and drag-to-external."""
        if self._is_panning:
            # Calculate delta in viewport coordinates
            current_pos = event.position()
            delta = current_pos - self._last_pan_point

            # Translate the view (scrolling)
            # Note: QGraphicsView scrolling is opposite to mouse movement
            hs = self.horizontalScrollBar()
            vs = self.verticalScrollBar()
            hs.setValue(hs.value() - round(delta.x()))
            vs.setValue(vs.value() - round(delta.y()))

            # Update the last pan point
            self._last_pan_point = current_pos
            event.accept()
        elif self._drag_start_pos is not None:
            # Check if we should start a drag-to-external operation
            current_pos = event.position()
            distance = (current_pos - self._drag_start_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._start_external_drag()
                self._drag_start_pos = None  # Reset after starting drag
                event.accept()
            else:
                super().mouseMoveEvent(event)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        """Handle mouse release events for panning and drag-to-external."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = None  # Clear drag start position
            if self._is_panning:
                self._is_panning = False
                self.setCursor(Qt.CursorShape.ArrowCursor) # Or keep custom cursor if desired
                event.accept()
            else:
                super().mouseReleaseEvent(event)
        else:
            super().mouseReleaseEvent(event)

    def _start_external_drag(self):
        """Initiate a drag operation to external applications."""
        # Determine which image path to use
        current_image_path = self.dropped_image_path or (
            self.image_gallery.last_selected_image_path if hasattr(self.image_gallery, 'last_selected_image_path') else None
        )
        
        if not current_image_path or not Path(current_image_path).exists():
            print("DragDropArea: Cannot start external drag - no valid image path")
            return
        
        print(f"DragDropArea: Starting external drag for: {current_image_path}")
        
        # Create mime data with the file URL
        mime_data = QMimeData()
        file_url = QUrl.fromLocalFile(current_image_path)
        mime_data.setUrls([file_url])
        
        # Create and execute the drag operation
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        
        # Create a thumbnail for the drag preview
        if self._full_res_pixmap and not self._full_res_pixmap.isNull():
            preview_size = 128
            preview_pixmap = self._full_res_pixmap.scaled(
                preview_size, preview_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            drag.setPixmap(preview_pixmap)
        
        # Execute the drag (Copy action is default for file drags)
        drag.exec(Qt.DropAction.CopyAction)

    # --- Drag and Drop ---
    def dragEnterEvent(self, event: QDragEnterEvent):
        mime_data = event.mimeData()
        # print(f"[DEBUG] DragEnter: Mime types: {mime_data.formats()}")

        accepted = False
        
        # 1. Check for Local Files
        if mime_data.hasUrls():
            urls = mime_data.urls()
            for url in urls:
                if url.isLocalFile():
                    local_path = url.toLocalFile()
                    if local_path.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS):
                        accepted = True
                        break
                else:
                    # Accept remote URLs (http/https)
                    if url.scheme() in ('http', 'https'):
                        accepted = True
                        break
        
        # 2. Check for Image Data
        if not accepted and mime_data.hasImage():
            accepted = True

        if accepted:
            event.acceptProposedAction()
            # Change background color for visual feedback
            self.setStyleSheet("QGraphicsView { background-color: #e0f0ff; }")
            return

        event.ignore()


    def dragLeaveEvent(self, event):
        self.setStyleSheet("") # Reset style
        super().dragLeaveEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent):
        """Handles drag move events to continuously accept valid image drags."""
        # Re-use logic from dragEnterEvent implicitly by accepting if accepted in enter
        # But we should re-check to be safe and consistent
        mime_data = event.mimeData()
        accepted = False
        
        if mime_data.hasUrls():
            urls = mime_data.urls()
            for url in urls:
                if url.isLocalFile():
                    if url.toLocalFile().lower().endswith(SUPPORTED_IMAGE_EXTENSIONS):
                        accepted = True; break
                elif url.scheme() in ('http', 'https'):
                    accepted = True; break
        
        if not accepted and mime_data.hasImage():
            accepted = True

        if accepted:
            event.acceptProposedAction()
            return
        
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        self.setStyleSheet("") # Reset style
        mime_data = event.mimeData()
        
        # 1. Check for Local Files (Priority 1)
        if mime_data.hasUrls():
            # Check if there's at least one local file
            for url in mime_data.urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if path.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS):
                        self._process_dropped_or_pasted_image(path)
                        event.acceptProposedAction()
                        return

        # 2. Check for Image Data (Priority 2 - Faster than download)
        # If the browser provides the image data directly (even during drag), use it.
        if mime_data.hasImage():
            image = QImage(mime_data.imageData())
            if not image.isNull():
                print("DragDropArea: Dropped raw image data (using instead of URL download).")
                self._save_and_process_pasted_image(image)
                event.acceptProposedAction()
                return
        
        # 3. Check for Remote URLs (Priority 3 - Fallback to download)
        if mime_data.hasUrls():
             url = mime_data.urls()[0]
             if url.scheme() in ('http', 'https'):
                print(f"DragDropArea: Dropped remote URL (Image data not found/valid): {url.toString()}")
                self._download_and_process_image_url(url.toString())
                event.acceptProposedAction()
                return

        event.ignore()

    def keyPressEvent(self, event: QKeyEvent):
        """Handle paste (Ctrl+V) events."""
        if event.matches(QKeySequence.StandardKey.Paste):
            self._handle_paste_event()
        else:
            super().keyPressEvent(event)

    def _handle_paste_event(self):
        """Processes clipboard content for images, file paths, or image URLs."""
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        
        print(f"DragDropArea: Paste event. Available formats: {mime_data.formats()}")

        # 1. Check for Local Files (e.g. copied from Explorer)
        if mime_data.hasUrls():
            urls = mime_data.urls()
            for url in urls:
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if path.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS):
                        print(f"DragDropArea: Pasted local file path: {path}")
                        self._process_dropped_or_pasted_image(path)
                        return

        # 2. Check for Raw Image Data (e.g. "Copy Image" from browser/app)
        if mime_data.hasImage():
            image = clipboard.image()
            if not image.isNull():
                print("DragDropArea: Pasted raw image data (hasImage=True).")
                self._save_and_process_pasted_image(image)
                return
        
        # 3. Fallback: Check specific image mime types
        for fmt in mime_data.formats():
            if fmt.startswith('image/'):
                print(f"DragDropArea: Found image mime type: {fmt}")
                data = mime_data.data(fmt)
                image = QImage.fromData(data)
                if not image.isNull():
                    print(f"DragDropArea: Successfully loaded image from data ({fmt}).")
                    self._save_and_process_pasted_image(image)
                    return

        # 4. Check for Image URLs (e.g. "Copy Image Link" or sometimes "Copy Image" puts URL in text)
        if mime_data.hasText():
            text = mime_data.text().strip()
            # Basic check if it looks like a URL and has an image extension
            # Note: Some URLs might not have extensions, but we start with this safety check
            if text.startswith(('http://', 'https://')):
                 print(f"DragDropArea: Found URL in clipboard: {text}")
                 # Try to download it
                 self._download_and_process_image_url(text)
                 return

        print("DragDropArea: Clipboard does not contain a supported image, file path, or URL.")

    def _download_and_process_image_url(self, url: str):
        """Downloads an image from a URL to a temp file and processes it."""
        print(f"DragDropArea: Attempting to download image from {url}")
        
        # Notify user of download start
        if hasattr(self.image_gallery, 'updateInfoTextSignal'):
             self.image_gallery.updateInfoTextSignal.emit(f"Downloading image from clipboard URL...")

        def download_task():
            try:
                response = requests.get(url, stream=True, timeout=10)
                response.raise_for_status()
                
                # Try to guess extension from content-type or url
                content_type = response.headers.get('content-type', '')
                ext = '.png' # Default
                if 'image/jpeg' in content_type: ext = '.jpg'
                elif 'image/webp' in content_type: ext = '.webp'
                elif 'image/gif' in content_type: ext = '.gif'
                elif url.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS):
                    ext = Path(url).suffix
                
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                temp_filename = f"downloaded_{timestamp}{ext}"
                temp_path = config.TEMP_DIR / temp_filename
                config.TEMP_DIR.mkdir(parents=True, exist_ok=True)

                with open(temp_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                return str(temp_path)
            except Exception as e:
                print(f"DragDropArea: Download failed: {e}")
                return None

        def handle_download_result(path):
            if path:
                print(f"DragDropArea: Download successful: {path}")
                self._process_dropped_or_pasted_image(path)
            else:
                if hasattr(self.image_gallery, 'updateInfoTextSignal'):
                    self.image_gallery.updateInfoTextSignal.emit(f"Failed to download image from clipboard URL.")

        # Use the existing threadpool from image_gallery if available, or create a worker
        # Since DragDropArea doesn't have direct access to threadpool, we can use the one in image_gallery
        if hasattr(self.image_gallery, 'threadpool'):
             from utils.workers import Worker # Ensure Worker is available
             worker = Worker(download_task)
             worker.signals.finished.connect(handle_download_result)
             self.image_gallery.threadpool.start(worker)
        else:
             print("DragDropArea: Cannot download - no threadpool available.")

    def _save_and_process_pasted_image(self, image):
        """Saves raw image data to a temp file and processes it."""
        try:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            temp_filename = f"pasted_{timestamp}.png"
            temp_path = config.TEMP_DIR / temp_filename
            
            # Ensure temp dir exists (it should from config.py, but safety first)
            config.TEMP_DIR.mkdir(parents=True, exist_ok=True)

            if image.save(str(temp_path), "PNG"):
                print(f"DragDropArea: Saved pasted image to {temp_path}")
                self._process_dropped_or_pasted_image(str(temp_path))
            else:
                print("DragDropArea: Failed to save pasted image to temp file.")
        except Exception as e:
            print(f"DragDropArea: Error saving pasted image: {e}")

    def _process_dropped_or_pasted_image(self, path_to_process: str):
        """Common logic for handling a new image path (from drop or paste)."""
        # print(f"[DEBUG] DragDropArea: Processing path: {path_to_process}")
        self.dropped_image_path = path_to_process
        self.temporary_predictions = None # Clear old predictions

        # Load the image using QPixmap
        pixmap = QPixmap(path_to_process)
        if pixmap.isNull():
                print(f"[ERROR] DragDropArea: QPixmap load FAILED for: {path_to_process}")
                self.set_image(None) # Show placeholder on load failure
                return

        # This triggers LOD generation and fitting
        self.set_image(pixmap)

        # Show processing message immediately
        if hasattr(self.image_gallery, 'updateInfoTextSignal'):
            # Use the signal that sets text and scrolls to top for consistency
            self.image_gallery.imageInfoSignal.emit(f"Processing {os.path.basename(path_to_process)}...", path_to_process)

        # Notify the main gallery to process metadata/tags etc.
        if hasattr(self.image_gallery, 'process_image_info'):
            # Use the callback to receive temporary predictions asynchronously if needed
            self.image_gallery.process_image_info(
                path_to_process,
                analyze=True, # Assume analysis is wanted on drop/paste
                store_temp_predictions_callback=self.set_temporary_predictions
            )


    # --- Callback and Context Menu ---

    def set_temporary_predictions(self, predictions: Optional[List['TagPrediction']]):
        """Callback to receive temporary analysis results."""
        print(f"DragDropArea: Received temporary predictions ({len(predictions) if predictions else 'None'})")
        self.temporary_predictions = predictions
        # Optionally trigger similarity search automatically *after* analysis completes
        if predictions is not None and self.dropped_image_path:
            print(f"DragDropArea: Automatically triggering similarity search for dropped image: {self.dropped_image_path}")
            if hasattr(self.image_gallery, 'perform_search'):
                 self.image_gallery.perform_search(
                    similarity_search=True,
                    similar_image_path=self.dropped_image_path,
                    tags=self.temporary_predictions # Use the fresh predictions
                )
            else:
                 print("DragDropArea: Error - ImageGallery reference invalid or missing 'perform_search'.")
        elif self.dropped_image_path:
             # Analysis might have failed or returned None/empty
             print("DragDropArea: Analysis didn't yield predictions for dropped image, cannot trigger auto-search.")


    def contextMenuEvent(self, event):
        """Shows context menu, adjusting zoom action enablement based on new scale limits."""
        # scene_pos = self.mapToScene(event.pos()) # Map view coords to scene coords
        item_at_pos = self.itemAt(event.pos()) # Check which item is under cursor in view coords

        # Show menu only if the click is on the actual image item (not placeholder/background)
        if self._pixmap_item and item_at_pos == self._pixmap_item:
            context_menu = QMenu(self)

            # --- Zoom Actions ---
            zoom_in_action = QAction("Zoom In (+)", self)
            zoom_in_action.triggered.connect(lambda: self._manual_zoom(ZOOM_FACTOR))
            # Enable if current scale is less than the absolute max level
            zoom_in_action.setEnabled(self._current_view_scale < MAX_ZOOM_LEVEL - FIT_SCALE_TOLERANCE)
            context_menu.addAction(zoom_in_action)

            zoom_out_action = QAction("Zoom Out (-)", self)
            zoom_out_action.triggered.connect(lambda: self._manual_zoom(1.0 / ZOOM_FACTOR))
            # Enable if current scale is greater than the scale needed to fit the image
            zoom_out_action.setEnabled(self._current_view_scale > self._fit_scale_full_res + FIT_SCALE_TOLERANCE)
            context_menu.addAction(zoom_out_action)

            fit_view_action = QAction("Fit to View (Reset Zoom)", self)
            fit_view_action.triggered.connect(self.fit_image_in_view)
            # Enable if current scale is significantly different from the fit scale
            fit_view_action.setEnabled(abs(self._current_view_scale - self._fit_scale_full_res) > FIT_SCALE_TOLERANCE)
            context_menu.addAction(fit_view_action)

            context_menu.addSeparator()

            # --- Image Actions ---
            search_similar_action = QAction("Search Similar Images", self)
            search_similar_action.triggered.connect(self.search_similar_images)
            # Enable if either a dropped image exists OR an image was last selected in the gallery
            # The context menu appears over the image, so we prioritize the dropped one if present.
            search_similar_action.setEnabled(bool(self.dropped_image_path or self.image_gallery.last_selected_image_path))
            context_menu.addAction(search_similar_action)

            # --- Tag Management ---
            manage_tags_action = QAction("Manage Tags...", self)
            # Only enable if there's a valid path in the database (usually dropped image is not yet in DB unless dragged FROM gallery or already there)
            # For simplicity, we enable if a path exists, the dialog handles "not in DB" gracefully or we assume user knows.
            # But technically, if it's a dropped file *not* in DB, we can't tag it yet.
            # However, if it was analyzed and added to DB (which DragDropArea triggers via process_image_info), it should be there.
            manage_tags_action.setEnabled(bool(self.dropped_image_path or self.image_gallery.last_selected_image_path))
            manage_tags_action.triggered.connect(self._open_manage_tags)
            context_menu.addAction(manage_tags_action)

            remove_image_action = QAction("Remove Image from Preview", self)
            remove_image_action.triggered.connect(self.remove_image)
            context_menu.addAction(remove_image_action)

            # --- File Actions ---
            # Use the dropped image path if available, otherwise fallback to gallery selection
            current_image_path = self.dropped_image_path or self.image_gallery.last_selected_image_path
            print(f"[DEBUG] DragDropArea.contextMenuEvent: current_image_path = '{current_image_path}'") # ADDED LOG
            if current_image_path and Path(current_image_path).exists(): # Check path validity
                 context_menu.addSeparator()
                 open_in_viewer_action = context_menu.addAction("Open in default viewer")
                 open_in_browser_action = context_menu.addAction("Show in file browser")
                 copy_name_action = context_menu.addAction("Copy image filename")
                 copy_image_action = context_menu.addAction("Copy image") # ADDED
                 copy_tags_action = context_menu.addAction("Copy tags") # ADDED
                 export_jpg_action = context_menu.addAction("Export as JPG...")
 
                 open_in_viewer_action.triggered.connect(lambda: self._open_in_viewer(current_image_path))
                 open_in_browser_action.triggered.connect(lambda: self._open_in_file_browser(current_image_path))
                 copy_name_action.triggered.connect(lambda: self._copy_image_name(current_image_path))
                 copy_image_action.triggered.connect(lambda checked=False, path=current_image_path: self.image_gallery._copy_image_to_clipboard(path)) # MODIFIED
                 copy_tags_action.triggered.connect(lambda checked=False, path=current_image_path: self.image_gallery._copy_tags_to_clipboard(path)) # MODIFIED
                 export_jpg_action.triggered.connect(lambda: self._export_as_jpg(current_image_path))

            # Execute the menu at the global cursor position
            context_menu.exec(event.globalPos())
        else:
            # If clicked outside the image item (e.g., on placeholder or empty area),
            # potentially show a different menu or no menu.
            # For simplicity, we can just call the base implementation or ignore.
            # super().contextMenuEvent(event)
            pass # No context menu if not on the image


    def _manual_zoom(self, factor: float):
        """Applies zoom factor from context menu, respecting limits, and updates LOD/item scale."""
        if not self._full_res_pixmap or not self._lods: return

        current_view_scale = self.transform().m11()
        potential_new_scale = current_view_scale * factor

        # Apply Zoom Limits (similar to wheelEvent)
        if potential_new_scale > MAX_ZOOM_LEVEL:
            factor = MAX_ZOOM_LEVEL / current_view_scale
            if factor <= 1.0 + FIT_SCALE_TOLERANCE: return # Already at max

        elif potential_new_scale < self._fit_scale_full_res - FIT_SCALE_TOLERANCE:
            self.fit_image_in_view()
            return

        # Apply Scaling to the VIEW only if factor is significant
        if abs(factor - 1.0) > FIT_SCALE_TOLERANCE:
            # Zoom towards the center of the viewport for context menu zoom
            center_point_view = self.viewport().rect().center()
            center_point_scene = self.mapToScene(center_point_view)

            self.scale(factor, factor)
            self._current_view_scale = self.transform().m11() # Update tracked VIEW scale

            # Recenter the view on the same scene point after scaling
            # Check if scene point is valid before centering
            if center_point_scene.x() != float('inf') and center_point_scene.y() != float('inf'):
                self.centerOn(center_point_scene)
            else:
                print("Warning: Invalid scene point during manual zoom recentering.")


            # --- Update LOD and item scale ---
            self._update_display_pixmap_and_item_scale()


    def search_similar_images(self):
        """Triggers a similarity search based on the currently displayed image."""
        print(f"DragDropArea: search_similar_images called.")
        path_to_search = None
        tags_to_use = None

        if self.dropped_image_path:
            print(f"DragDropArea: Using dropped image for similarity search: {self.dropped_image_path}")
            path_to_search = self.dropped_image_path
            tags_to_use = self.temporary_predictions # Use predictions if available for dropped img
            if tags_to_use:
                 print(f"DragDropArea: Using {len(tags_to_use)} temporary predictions for search.")
            else:
                 print("DragDropArea: No temporary predictions available for dropped image (will use image embedding directly).")

        elif self.image_gallery.last_selected_image_path:
            print(f"DragDropArea: No dropped image, using last selected image: {self.image_gallery.last_selected_image_path}")
            path_to_search = self.image_gallery.last_selected_image_path
            # For last selected image, we generally assume tags are in DB, so don't pass temporary ones
            tags_to_use = None # Let backend handle tag lookup if needed

        if path_to_search and hasattr(self.image_gallery, 'perform_search'):
            self.image_gallery.perform_search(
                similarity_search=True,
                similar_image_path=path_to_search,
                tags=tags_to_use # Pass None if not available or not applicable
            )
        else:
            if not path_to_search:
                print("DragDropArea: No image available (dropped or selected) for similarity search.")
            else:
                 print("DragDropArea: Error - ImageGallery reference invalid or missing 'perform_search'.")

    def _open_manage_tags(self):
        """Helper to open manage tags dialog for current image."""
        current_image_path = self.dropped_image_path or self.image_gallery.last_selected_image_path
        if current_image_path and hasattr(self.image_gallery, 'open_manage_tags_dialog'):
             self.image_gallery.open_manage_tags_dialog(current_image_path)

    def remove_image(self):
        """Clears the displayed image, LODs, and associated data, showing the placeholder."""
        print("DragDropArea: Remove Image clicked")
        self.set_image(None) # This clears internal image data
        self._show_placeholder_text() # Explicitly show placeholder now
        self.dropped_image_path = None
        self.temporary_predictions = None
        print(f"DragDropArea: Preview cleared.")

    # --- Helper methods for context menu file actions (No changes needed) ---
    def _open_in_viewer(self, image_path: str):
        """Opens the image file using the system's default application."""
        try:
            file_path = Path(image_path)
            if not file_path.exists():
                print(f"Error opening viewer: File not found at {image_path}")
                return
            if sys.platform == "win32":
                os.startfile(file_path)
            elif sys.platform == "darwin":
                subprocess.run(["open", str(file_path)], check=True, timeout=5)
            else: # Linux/other POSIX
                subprocess.run(["xdg-open", str(file_path)], check=True, timeout=5)
            print(f"Attempted to open {image_path} in default viewer.")
        except FileNotFoundError:
            print(f"Error opening viewer: File not found at {image_path}")
        except subprocess.TimeoutExpired:
             print(f"Error opening viewer: Command timed out for {image_path}")
        except Exception as e:
            print(f"Error opening image '{image_path}' in viewer: {e}")

    def _open_in_file_browser(self, image_path: str):
        """Opens the file browser and highlights the image file."""
        try:
            file_path = Path(image_path).resolve()
            if not file_path.exists():
                print(f"Error opening file browser: File not found at {file_path}")
                return

            print(f"Attempting to show {file_path} in file browser.")
            if sys.platform == "win32":
                # Explorer argument selects the file
                subprocess.run(['explorer', '/select,', str(file_path)], check=True)
            elif sys.platform == "darwin":
                # 'open -R' reveals the file in Finder
                subprocess.run(['open', '-R', str(file_path)], check=True)
            else: # Linux/other POSIX
                # xdg-open usually opens the *directory*
                # Try common file managers that might support selecting
                try:
                    # Try Nautilus/Files (GNOME)
                    subprocess.run(['nautilus', '--select', str(file_path)], check=True, timeout=3)
                except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    try:
                         # Try Dolphin (KDE)
                         subprocess.run(['dolphin', '--select', str(file_path)], check=True, timeout=3)
                    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                         try:
                              # Try Thunar (XFCE) - might just open dir
                              subprocess.run(['thunar', str(file_path.parent)], check=True, timeout=3)
                         except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                              # Fallback: open the parent directory
                              subprocess.run(['xdg-open', str(file_path.parent)], check=True, timeout=3)

        except FileNotFoundError:
             print(f"Error opening file browser: File or required command not found for {image_path}")
        except subprocess.TimeoutExpired:
             print(f"Error opening file browser: Command timed out for {image_path}")
        except Exception as e:
            print(f"Error opening file browser for '{image_path}': {e}")

    def _copy_image_name(self, image_path: str):
        """Copies the base filename of the image to the clipboard."""
        try:
            clipboard = QApplication.clipboard()
            if clipboard:
                filename = Path(image_path).name
                clipboard.setText(filename)
                print(f"Copied '{filename}' to clipboard.")
            else:
                print("Error copying filename: Could not access clipboard.")
        except Exception as e:
            print(f"Error copying filename for '{image_path}': {e}")

    def _export_as_jpg(self, image_path: str):
        """Opens a dialog (if available) to export the image as JPG."""
        try:
            # Attempt local import to avoid circular dependency issues if dialog uses main window stuff
            from gui.dialogs.export_jpg import ExportAsJPGDialog
            # Check if file exists before opening dialog
            if not Path(image_path).exists():
                 print(f"Error exporting: Source file not found at {image_path}")
                 # Optionally show a message box to the user
                 return

            print(f"Opening export dialog for: {image_path}")
            # Pass the main window instance (often needed for modality or context)
            # and the source path
            export_dialog = ExportAsJPGDialog(self.image_gallery, image_path)
            export_dialog.exec() # Show the dialog modally
            print("Export dialog closed.")

        except ImportError:
            print("Error: Could not import ExportAsJPGDialog. Export feature unavailable.")
            # Potentially show a message box to the user
        except FileNotFoundError:
             print(f"Error exporting: Source file not found during dialog init for {image_path}")
        except Exception as e:
            print(f"Error opening export dialog for '{image_path}': {e}")