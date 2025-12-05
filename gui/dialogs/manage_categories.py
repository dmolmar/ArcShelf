from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QMessageBox, QWidget, QInputDialog
)
from PyQt6.QtCore import Qt
from database.db_manager import Database

class ManageCategoriesDialog(QDialog):
    def __init__(self, parent: QWidget, db: Database):
        super().__init__(parent)
        self.setWindowTitle("Manage Categories")
        self.resize(400, 500)
        self.db = db

        self.layout = QVBoxLayout(self)

        # Instructions
        self.layout.addWidget(QLabel("Manage Tag Categories"))

        # --- Add Category Section ---
        add_layout = QHBoxLayout()
        self.cat_input = QLineEdit()
        self.cat_input.setPlaceholderText("New category name...")
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self.add_category)
        
        add_layout.addWidget(self.cat_input)
        add_layout.addWidget(self.add_button)
        self.layout.addLayout(add_layout)

        # --- Categories List ---
        self.cat_list = QListWidget()
        self.layout.addWidget(self.cat_list)

        # --- Delete Section ---
        delete_layout = QHBoxLayout()
        self.delete_button = QPushButton("Delete Selected")
        self.delete_button.clicked.connect(self.delete_category)
        delete_layout.addStretch()
        delete_layout.addWidget(self.delete_button)
        self.layout.addLayout(delete_layout)

        # --- Close Button ---
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        self.layout.addWidget(self.close_button)

        self.refresh_list()

    def refresh_list(self):
        self.cat_list.clear()
        categories = self.db.get_all_categories()
        self.cat_list.addItems(categories)

    def add_category(self):
        name = self.cat_input.text().strip().lower()
        if not name:
            return

        # Validation against reserved keywords
        reserved = ["character", "rating", "general", "artist", "copyright", "meta"]
        # Actually, user wants to prevent conflict. If it's already in the list, it's a conflict.
        # But specifically "character" and "rating" are special model tags.
        # "general", "artist", "copyright", "meta" are standard booru categories.
        # The user said: "make sure that there is no conflict between category names and the special ai model tags 'character', 'rating', etc."
        
        if name in ["character", "rating"]:
             QMessageBox.warning(self, "Invalid Name", f"'{name}' is a reserved system category.")
             return

        if self.db.add_category(name):
            self.cat_input.clear()
            self.refresh_list()
        else:
            QMessageBox.warning(self, "Error", "Category already exists or could not be added.")

    def delete_category(self):
        selected_items = self.cat_list.selectedItems()
        if not selected_items:
            return

        name = selected_items[0].text()
        
        # Prevent deleting default categories?
        defaults = ["general", "character", "artist", "copyright", "meta", "rating"]
        if name in defaults:
             QMessageBox.warning(self, "Restricted", f"Cannot delete default category '{name}'.")
             return

        confirm = QMessageBox.question(
            self, "Confirm Delete",
            f"Are you sure you want to delete category '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if confirm == QMessageBox.StandardButton.Yes:
            if self.db.delete_category(name):
                self.refresh_list()
            else:
                QMessageBox.warning(self, "Error", "Could not delete category.")
