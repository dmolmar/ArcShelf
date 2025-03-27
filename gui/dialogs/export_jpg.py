import os
import math
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QSlider,
    QLabel, QFileDialog, QMessageBox, QFrame, QDoubleSpinBox, QWidget
)
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import Qt, QSize
from PIL import Image, UnidentifiedImageError

# Import config to potentially get base directory for icon
from ... import config

class ExportAsJPGDialog(QDialog):
    """
    A dialog window for exporting an image as a JPG file with quality
    and resolution options.
    """
    def __init__(self, parent: Optional[QWidget], image_path: str):
        """
        Initializes the ExportAsJPGDialog.

        Args:
            parent: The parent widget.
            image_path: The path to the image file being exported.
        """
        super().__init__(parent)
        self.title = "Export as JPG"
        self.image_path = image_path
        self.orig_width = 1
        self.orig_height = 1
        self.aspect_ratio = 1.0

        # Try to load original dimensions and calculate aspect ratio
        try:
            with Image.open(image_path) as img:
                self.orig_width, self.orig_height = img.size
                if self.orig_height > 0:
                    self.aspect_ratio = self.orig_width / self.orig_height
                else:
                    raise ValueError("Image height cannot be zero.")
        except (FileNotFoundError, UnidentifiedImageError, ValueError) as e:
            print(f"Error loading image dimensions for export: {e}")
            # Keep default aspect ratio, maybe disable resolution controls?
            QMessageBox.warning(self, "Error", f"Could not load image details:\n{e}")
            # Consider closing the dialog or disabling controls if image is invalid
            # self.close() # Or disable relevant widgets

        # Initial target resolution in Megapixels
        self.target_mp = 2.0
        self.new_width = 0
        self.new_height = 0
        self.calculate_dimensions() # Calculate initial dimensions based on target_mp

        # Suggest default output filename
        self.default_filename = Path(image_path).stem + '.jpg'

        self.setup_ui()
        self.setModal(True) # Make the dialog modal

    def calculate_dimensions(self):
        """Calculates target width and height based on target megapixels and aspect ratio."""
        if self.aspect_ratio <= 0: return # Avoid division by zero if aspect ratio is invalid

        total_pixels = self.target_mp * 1_000_000
        # Calculate width first, ensuring it's at least 1
        self.new_width = max(1, int(math.sqrt(total_pixels * self.aspect_ratio)))
        # Calculate height based on width and aspect ratio, ensuring it's at least 1
        self.new_height = max(1, int(self.new_width / self.aspect_ratio))

    def setup_ui(self):
        """Sets up the user interface elements of the dialog."""
        self.setWindowTitle(self.title)

        # Set window icon using relative path from config if available
        icon_path = config.BASE_DIR / "arcueid.ico"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        else:
            print(f"Warning: Icon file not found at {icon_path}")

        layout = QVBoxLayout(self)

        # --- Output Path ---
        path_frame = QFrame(self)
        path_layout = QHBoxLayout(path_frame)
        path_layout.setContentsMargins(0,0,0,0)
        path_label = QLabel("Save to:", path_frame)
        self.path_entry = QLineEdit(path_frame)
        self.path_entry.setPlaceholderText("Select output file path...")
        browse_btn = QPushButton("Browse...", path_frame)
        browse_btn.clicked.connect(self.browse_output)
        path_layout.addWidget(path_label)
        path_layout.addWidget(self.path_entry)
        path_layout.addWidget(browse_btn)
        layout.addWidget(path_frame)

        # --- Quality Slider ---
        quality_frame = QFrame(self)
        quality_layout = QHBoxLayout(quality_frame)
        quality_layout.setContentsMargins(0,0,0,0)
        quality_label_text = QLabel("Quality:", quality_frame)
        self.quality_slider = QSlider(Qt.Orientation.Horizontal, quality_frame)
        self.quality_slider.setRange(1, 100)
        self.quality_slider.setValue(85) # Default quality
        self.quality_slider.setTickInterval(10)
        self.quality_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.quality_value_label = QLabel(f"{self.quality_slider.value()}%", quality_frame)
        self.quality_value_label.setMinimumWidth(35) # Ensure space for "100%"
        self.quality_slider.valueChanged.connect(self.update_quality_label)
        quality_layout.addWidget(quality_label_text)
        quality_layout.addWidget(self.quality_slider)
        quality_layout.addWidget(self.quality_value_label)
        layout.addWidget(quality_frame)

        # --- Resolution (Megapixels) SpinBox ---
        res_frame = QFrame(self)
        res_layout = QHBoxLayout(res_frame)
        res_layout.setContentsMargins(0,0,0,0)
        res_label_text = QLabel("Target Res (MP):", res_frame)
        self.res_spinbox = QDoubleSpinBox(res_frame)
        self.res_spinbox.setRange(0.1, 50.0) # Allow higher MP targets
        self.res_spinbox.setSingleStep(0.1)
        self.res_spinbox.setDecimals(1)
        self.res_spinbox.setValue(self.target_mp)
        self.res_spinbox.valueChanged.connect(self.on_res_change)
        # Display calculated dimensions
        self.res_display_label = QLabel(f"({self.new_width} x {self.new_height})", res_frame)
        res_layout.addWidget(res_label_text)
        res_layout.addWidget(self.res_spinbox)
        res_layout.addWidget(self.res_display_label)
        layout.addWidget(res_frame)

        # --- Export Button ---
        export_btn = QPushButton("Export", self)
        export_btn.clicked.connect(self.export)
        # Add a cancel button maybe? Standard dialogs often have OK/Cancel
        # cancel_btn = QPushButton("Cancel", self)
        # cancel_btn.clicked.connect(self.reject)
        # button_layout = QHBoxLayout()
        # button_layout.addStretch()
        # button_layout.addWidget(cancel_btn)
        # button_layout.addWidget(export_btn)
        # layout.addLayout(button_layout)
        layout.addWidget(export_btn, alignment=Qt.AlignmentFlag.AlignRight) # Simpler for now

        self.setLayout(layout)
        self.resize(QSize(400, 150)) # Set a reasonable initial size

    def browse_output(self):
        """Opens a file dialog to select the output JPG path."""
        initial_dir = os.path.dirname(self.image_path)
        # Suggest initial filename in the dialog
        suggested_path = os.path.join(initial_dir, self.default_filename)

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Image As", # Dialog title
            suggested_path,  # Starting directory and filename
            "JPEG Image (*.jpg *.jpeg)" # Filter
        )
        if filename:
            # Ensure the filename ends with .jpg
            if not filename.lower().endswith(('.jpg', '.jpeg')):
                filename += '.jpg'
            self.path_entry.setText(filename)

    def update_quality_label(self):
        """Updates the label showing the current quality slider value."""
        self.quality_value_label.setText(f"{self.quality_slider.value()}%")

    def on_res_change(self, value: float):
        """Updates target megapixels, recalculates dimensions, and updates the display label."""
        self.target_mp = value
        self.calculate_dimensions()
        self.res_display_label.setText(f"({self.new_width} x {self.new_height})")

    def export(self):
        """Performs the image export operation."""
        output_path = self.path_entry.text().strip()
        if not output_path:
            QMessageBox.warning(self, "Input Error", "Please select or enter an output file path.")
            return

        # Ensure output directory exists (optional, QFileDialog usually handles this)
        # output_dir = Path(output_path).parent
        # output_dir.mkdir(parents=True, exist_ok=True)

        quality = self.quality_slider.value()

        try:
            with Image.open(self.image_path) as img:
                # Convert to RGB if necessary (JPG doesn't support transparency)
                if img.mode in ("RGBA", "LA", "P"):
                    print(f"Converting image from {img.mode} to RGB for JPG export.")
                    # Create a white background image
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    try:
                        # Paste image onto background, using alpha mask if available
                        bg.paste(img, mask=img.split()[-1] if 'A' in img.mode else None)
                        img = bg
                    except Exception as paste_err:
                         print(f"Error during alpha compositing, converting directly: {paste_err}")
                         img = img.convert("RGB") # Fallback direct conversion
                elif img.mode != "RGB":
                     img = img.convert("RGB")


                # Resize the image using LANCZOS for high quality
                print(f"Resizing image to {self.new_width}x{self.new_height}")
                resized_img = img.resize((self.new_width, self.new_height), Image.Resampling.LANCZOS)

                # Save as JPEG
                print(f"Saving image to {output_path} with quality {quality}")
                resized_img.save(output_path, "JPEG", quality=quality, optimize=True, progressive=True) # Add optimize/progressive

            QMessageBox.information(self, "Export Successful", f"Image successfully exported to:\n{output_path}")
            self.accept() # Close the dialog successfully
        except FileNotFoundError:
             QMessageBox.critical(self, "Error", f"Source image not found:\n{self.image_path}")
        except UnidentifiedImageError:
             QMessageBox.critical(self, "Error", f"Could not read source image (unsupported format or corrupt):\n{self.image_path}")
        except Exception as e:
            error_message = f"An error occurred during export:\n{e}"
            print(error_message)
            traceback.print_exc() # Print full traceback to console for debugging
            QMessageBox.critical(self, "Export Error", error_message)
            # Do not close the dialog on error, let the user retry or cancel

# Example usage (for testing standalone)
if __name__ == '__main__':
    import sys
    from PyQt6.QtWidgets import QApplication

    # Create a dummy image path for testing
    # Replace with a real image path on your system
    test_image_path = "path/to/your/test_image.png" # CHANGE THIS

    if not Path(test_image_path).is_file():
         print(f"Please update 'test_image_path' in the example usage section of export_jpg.py to a valid image file.")
    else:
        app = QApplication(sys.argv)
        # Pass None as parent if running standalone
        dialog = ExportAsJPGDialog(None, test_image_path)
        dialog.show()
        sys.exit(app.exec())