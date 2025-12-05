from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QListWidget, QListWidgetItem, QMessageBox,
    QCompleter, QWidget
)
from PyQt6.QtCore import Qt, QStringListModel
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QColor
from database.db_manager import Database

class ManageTagsDialog(QDialog):
    def __init__(self, parent: QWidget, image_path: str, db: Database):
        super().__init__(parent)
        self.setWindowTitle("Manage Tags")
        self.resize(500, 600)
        self.image_path = image_path
        self.db = db

        self.layout = QVBoxLayout(self)

        # Image Info Label
        self.info_label = QLabel(f"Manage tags for:\n{self.image_path}")
        self.info_label.setWordWrap(True)
        self.layout.addWidget(self.info_label)

        # --- Add New Tag Section ---
        add_layout = QHBoxLayout()

        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("Enter new tag...")
        self.setup_completer() # Setup auto-completion

        self.category_combo = QComboBox()
        self.category_combo.setEditable(True) # Allow typing new categories
        self._populate_categories()
        self.category_combo.setToolTip("Select or type a category")

        self.add_button = QPushButton("Add Tag")
        self.add_button.clicked.connect(self.add_tag)

        add_layout.addWidget(self.tag_input)
        add_layout.addWidget(self.category_combo)
        add_layout.addWidget(self.add_button)
        self.layout.addLayout(add_layout)

        # --- Tags List ---
        self.tags_list = QListWidget()
        self.tags_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.layout.addWidget(self.tags_list)

        # --- Remove Section ---
        remove_layout = QHBoxLayout()
        self.remove_button = QPushButton("Remove Selected Tags")
        self.remove_button.clicked.connect(self.remove_selected_tags)
        remove_layout.addStretch()
        remove_layout.addWidget(self.remove_button)
        self.layout.addLayout(remove_layout)

        # --- Close Button ---
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        self.layout.addWidget(self.close_button)

        # Initial Load
        self.refresh_tags_list()

    def setup_completer(self):
        """Initial setup for the completer. Actual data loading happens in refresh_completer."""
        self.refresh_completer()

    def refresh_completer(self):
        # Fetch all unique tag names for autocomplete
        try:
            all_tags = self.db.get_all_tag_names()
            completer = QCompleter(all_tags, self)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self.tag_input.setCompleter(completer)
        except Exception as e:
            print(f"Error loading tag suggestions: {e}")

    def _populate_categories(self):
        """Populates the category combo box with existing and default categories."""
        defaults = ["general", "character", "artist", "copyright", "meta"]
        existing = self.db.get_all_categories()

        # Combine and deduplicate, keeping defaults first for convenience
        combined = list(dict.fromkeys(defaults + existing))
        self.category_combo.clear()
        self.category_combo.addItems(combined)

    def refresh_tags_list(self):
        self.tags_list.clear()

        # Refresh categories in case new ones were added
        self._populate_categories()

        try:
            rows = self.db.get_tags_with_manual_status(self.image_path)

            for name, category, is_manual in rows:
                display_text = f"{name} ({category})"
                if is_manual:
                    display_text += " [MANUAL]"

                item = QListWidgetItem(display_text)
                item.setData(Qt.ItemDataRole.UserRole, name) # Store tag name

                if is_manual:
                    # Make manual tags visually distinct
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    # Use a desaturated blue as requested (#2a7da3) instead of strict blue
                    item.setForeground(QColor("#2a7da3"))

                self.tags_list.addItem(item)
        except Exception as e:
            print(f"Error refreshing tags list: {e}")

        self.refresh_completer() # Also refresh autocomplete

    def add_tag(self):
        tag_name = self.tag_input.text().strip()
        category = self.category_combo.currentText().strip()

        if not tag_name or not category:
            return

        self.db.add_manual_tag(self.image_path, tag_name, category)
        self.tag_input.clear()
        self.refresh_tags_list()

    def remove_selected_tags(self):
        selected_items = self.tags_list.selectedItems()
        if not selected_items:
            return

        count = len(selected_items)
        confirm = QMessageBox.question(
            self, "Confirm Remove",
            f"Are you sure you want to remove {count} tags?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if confirm == QMessageBox.StandardButton.Yes:
            for item in selected_items:
                tag_name = item.data(Qt.ItemDataRole.UserRole)
                self.db.remove_tag(self.image_path, tag_name)
            self.refresh_tags_list()
