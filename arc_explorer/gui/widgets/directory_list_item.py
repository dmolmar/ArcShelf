from typing import Optional

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QCheckBox
from PyQt6.QtCore import pyqtSignal, Qt

class DirectoryListItem(QWidget):
    """
    A custom widget representing a directory in a list, with a checkbox
    to indicate its active state (e.g., for inclusion in searches).
    """
    # Signal emitted when the checkbox state changes
    # Arguments: directory_path (str), is_active (bool)
    stateChanged = pyqtSignal(str, bool)

    def __init__(self, directory: str, is_checked: bool = True, parent: Optional[QWidget] = None):
        """
        Initializes the DirectoryListItem.

        Args:
            directory: The directory path string.
            is_checked: The initial checked state of the checkbox. Defaults to True.
            parent: The parent widget. Defaults to None.
        """
        super().__init__(parent)
        self.directory = directory

        self.layout = QHBoxLayout(self)
        # Reduce margins for tighter packing in the list
        self.layout.setContentsMargins(2, 2, 2, 2) # Small margins

        # Directory Label (takes up available space)
        self.directory_label = QLabel(self.directory)
        self.directory_label.setToolTip(self.directory) # Show full path on hover
        self.layout.addWidget(self.directory_label)

        # Checkbox for active state (aligned to the right)
        self.active_checkbox = QCheckBox()
        self.active_checkbox.setChecked(is_checked)
        # Connect the Qt signal to our custom handler
        self.active_checkbox.stateChanged.connect(self._handle_state_changed)
        # Add checkbox with right alignment
        self.layout.addWidget(self.active_checkbox, alignment=Qt.AlignmentFlag.AlignRight)

        # Set layout on the widget
        self.setLayout(self.layout)

    def _handle_state_changed(self, qt_state: int):
        """
        Internal slot connected to the checkbox's stateChanged signal.
        Determines the boolean state and emits the custom stateChanged signal.
        """
        is_active = (qt_state == Qt.CheckState.Checked.value) # Compare with the enum value
        self.stateChanged.emit(self.directory, is_active)

    def setChecked(self, is_checked: bool):
        """Programmatically sets the checked state of the checkbox."""
        self.active_checkbox.setChecked(is_checked)

    def isChecked(self) -> bool:
        """Returns the current checked state of the checkbox."""
        return self.active_checkbox.isChecked()

    def getDirectory(self) -> str:
        """Returns the directory path associated with this item."""
        return self.directory