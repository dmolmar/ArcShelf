from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QListWidget, QListWidgetItem, QMessageBox,
    QCompleter, QWidget
)
from PyQt6.QtCore import Qt, QStringListModel
from PyQt6.QtGui import QStandardItemModel, QStandardItem
from database.db_manager import Database

class ManageTagsDialog(QDialog):
    # Class variables to remember last tag and category across instances
    _last_tag_name = ""
    _last_category = "general"

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
        self.tag_input.setText(ManageTagsDialog._last_tag_name)  # Set last used tag
        self.tag_input.textChanged.connect(self.on_tag_input_changed)  # Connect to auto-select category
        self.setup_completer() # Setup auto-completion

        self.category_combo = QComboBox()
        # self.category_combo.addItems(["general", "character", "artist", "copyright", "meta"]) # Loaded from DB now
        self.category_combo.setToolTip("Select category")
        self.refresh_categories() # Load categories
        # After categories are loaded, set to last used category
        self.set_category_to_last_used()

        self.manage_cats_button = QPushButton("Manage...")
        self.manage_cats_button.setFixedWidth(80)
        self.manage_cats_button.clicked.connect(self.open_manage_categories_dialog)

        self.add_button = QPushButton("Add Tag")
        self.add_button.clicked.connect(self.add_tag)

        add_layout.addWidget(self.tag_input)
        add_layout.addWidget(self.category_combo)
        add_layout.addWidget(self.manage_cats_button) # Added button
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
            with self.db.lock:
                import sqlite3
                with sqlite3.connect(self.db.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM tags")
                    all_tags = [row[0] for row in cursor.fetchall()]

            completer = QCompleter(all_tags, self)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self.tag_input.setCompleter(completer)
        except Exception as e:
            print(f"Error loading tag suggestions: {e}")

    def refresh_tags_list(self):
        self.tags_list.clear()
        rating, tags = self.db.get_image_info_by_path(self.image_path)

        if tags:
            # Sort: Manual tags first, then by category, then name
            # TagPrediction now has is_manual field
            sorted_tags = sorted(tags, key=lambda t: (not getattr(t, 'is_manual', False), t.category, t.tag))

            for tag in sorted_tags:
                display_text = f"{tag.tag} ({tag.category})"
                is_manual = getattr(tag, 'is_manual', False)
                if is_manual:
                    display_text += " [MANUAL]"

                item = QListWidgetItem(display_text)
                item.setData(Qt.ItemDataRole.UserRole, tag.tag) # Store tag name

                if is_manual:
                    # Make manual tags visually distinct
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setForeground(Qt.GlobalColor.blue) # Or similar

                self.tags_list.addItem(item)

        self.refresh_completer() # Also refresh autocomplete

    def on_tag_input_changed(self, text: str):
        """Auto-select category when typing an existing tag name."""
        tag_name = text.strip()
        if not tag_name:
            return
        
        # Check if this tag already exists in the database
        existing_category = self.db.get_tag_category(tag_name)
        if existing_category:
            # Auto-select the category
            index = self.category_combo.findText(existing_category)
            if index >= 0:
                self.category_combo.setCurrentIndex(index)

    def add_tag(self):
        tag_name = self.tag_input.text().strip()
        category = self.category_combo.currentText()

        if not tag_name:
            return

        self.db.add_manual_tag(self.image_path, tag_name, category)
        # Save last used tag and category
        ManageTagsDialog._last_tag_name = tag_name
        ManageTagsDialog._last_category = category
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

    def refresh_categories(self):
        current = self.category_combo.currentText()
        self.category_combo.clear()
        cats = self.db.get_all_categories()
        self.category_combo.addItems(cats)
        # Restore selection if possible, otherwise default to 'general'
        index = self.category_combo.findText(current)
        if index >= 0:
            self.category_combo.setCurrentIndex(index)
        else:
            gen_index = self.category_combo.findText("general")
            if gen_index >= 0: self.category_combo.setCurrentIndex(gen_index)

    def set_category_to_last_used(self):
        """Set the category combo box to the last used category."""
        index = self.category_combo.findText(ManageTagsDialog._last_category)
        if index >= 0:
            self.category_combo.setCurrentIndex(index)
        else:
            # Fallback to 'general' if last category doesn't exist
            gen_index = self.category_combo.findText("general")
            if gen_index >= 0:
                self.category_combo.setCurrentIndex(gen_index)

    def open_manage_categories_dialog(self):
        from .manage_categories import ManageCategoriesDialog
        dialog = ManageCategoriesDialog(self, self.db)
        dialog.exec()
        self.refresh_categories()
        self.set_category_to_last_used()  # Restore last used category after refresh
