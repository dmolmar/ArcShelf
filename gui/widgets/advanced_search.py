import re
from typing import Dict, Optional, TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLineEdit, QListWidget, QPushButton, QListWidgetItem
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QEvent, QObject, pyqtSlot
from PyQt6.QtGui import QFocusEvent

# Use TYPE_CHECKING for type hints if needed, though direct parent dependency is removed
# if TYPE_CHECKING:
#     from ..main_window import ImageGallery

class AdvancedSearchPanel(QWidget):
    """
    A widget providing an input field for advanced search queries,
    a button to trigger the search, and a list for displaying tag suggestions.
    """
    # Signal emitted when the user requests a search (e.g., clicks button or presses Enter)
    searchRequested = pyqtSignal(str)
    # Signal emitted when the text or cursor position changes (for triggering suggestion updates)
    inputChanged = pyqtSignal()

    tagSegmentSelected = pyqtSignal(str) # Emits the fully selected tag segment text

    requestHideSuggestions = pyqtSignal()

    # Ask ImageGallery if suggestions are visible and how many
    checkSuggestionVisibilityRequest = pyqtSignal() # Emitted to ask
    # Tell ImageGallery to navigate
    navigateSuggestions = pyqtSignal(str) # 'up' or 'down'

    confirmSuggestion = pyqtSignal() # Ask IG to confirm the currently highlighted item

    # Signal emitted specifically when the search field gains focus
    focusGained = pyqtSignal()
    # Signal emitted specifically when the search field loses focus
    focusLost = pyqtSignal() # Keep this, it will be emitted from eventFilter now

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._allow_enter_search = True # Flag to control search triggering
        self._suggestions_are_visible = False
        self._suggestions_count = 0

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        # Search Input Field
        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Enter search query (e.g., tag1 AND NOT tag2)...")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.installEventFilter(self)
        self.layout.addWidget(self.search_field)

        # Search Button
        self.search_button = QPushButton("Search")
        self.layout.addWidget(self.search_button)

        # --- Signal Connections ---
        self.search_button.clicked.connect(self._emit_search_request)
        self.search_field.returnPressed.connect(self._emit_search_request)
        self.search_field.textChanged.connect(self.inputChanged.emit)
        self.search_field.cursorPositionChanged.connect(self.inputChanged.emit)
        # self.search_field.setFocusPolicy(Qt.FocusPolicy.StrongFocus) # QLineEdit default is StrongFocus
    
    @pyqtSlot(bool, int)
    def receiveSuggestionVisibilityInfo(self, is_visible: bool, count: int):
        self._suggestions_are_visible = is_visible
        self._suggestions_count = count
    
    @pyqtSlot(bool)
    def handleSuggestionConfirmationFinished(self, confirmation_happened: bool):
        """
        Receives confirmation status from ImageGallery.
        Sets flag to allow/disallow search on current Enter press.
        """
        print(f"ASP: Received suggestionConfirmationFinished({confirmation_happened})")
        # If confirmation happened, the Enter press was used for that, so don't search.
        # If it didn't happen (no item selected), allow search.
        self._allow_enter_search = not confirmation_happened

        # If search is allowed now, manually trigger it (since we consumed the event)
        if self._allow_enter_search:
             print("  Confirmation didn't happen, triggering search manually.")
             self._emit_search_request()

    def eventFilter(self, watched_object: QObject, event: QEvent) -> bool:
        """Filters events for the search_field."""
        if watched_object is self.search_field:
            if event.type() == QEvent.Type.KeyPress:
                key = event.key()
                self.checkSuggestionVisibilityRequest.emit()
                suggestions_active = self._suggestions_are_visible and self._suggestions_count > 0

                if suggestions_active and (key == Qt.Key.Key_Down or key == Qt.Key.Key_Up):
                    direction = 'down' if key == Qt.Key.Key_Down else 'up'
                    self.navigateSuggestions.emit(direction)
                    return True

                elif key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
                    print("ASP: Intercepted Enter Key.")
                    if suggestions_active:
                        # Reset flag before asking for confirmation
                        self._allow_enter_search = False # Assume Enter is for confirmation first
                        print("  Suggestions active, emitting confirmSuggestion.")
                        self.confirmSuggestion.emit() # Ask IG to handle confirmation
                        # We MUST consume the event here, and wait for the signal back
                        # to decide if a search should happen *afterwards*.
                        return True
                    else:
                        # No suggestions active, allow default Enter behavior (triggers search)
                        print("  Suggestions not active, allowing default Enter.")
                        return False # Let QLineEdit handle it -> _emit_search_request
                    
            elif event.type() == QEvent.Type.FocusIn:
                print("AdvancedSearchPanel: search_field Focus In")
                self.focusGained.emit()
            elif event.type() == QEvent.Type.FocusOut:
                print("AdvancedSearchPanel: search_field Focus Out")
                self.focusLost.emit()
            elif event.type() == QEvent.Type.MouseButtonDblClick:
                print("AdvancedSearchPanel: Double Click Detected")
                # ... (keep existing double-click logic) ...
                click_char_pos = self.search_field.cursorPositionAt(event.pos())
                current_text = self.search_field.text()
                operators = {'AND', 'OR', 'NOT'}
                operator_found = False
                for op in operators:
                    # Find all occurrences of the operator (case-insensitive, whole word)
                    pattern = re.compile(r'\b' + op + r'\b', re.IGNORECASE)
                    for match in pattern.finditer(current_text):
                        op_start, op_end = match.span()
                        # Check if the double-click position falls within this operator match
                        if op_start <= click_char_pos < op_end:
                            print(f"  Double-click on Operator: '{match.group()}' ({op_start}-{op_end})")
                            # Select only the operator
                            self.search_field.setSelection(op_start, op_end - op_start)
                            # Ensure no suggestions are shown (by emitting focusLost? or just hiding?)
                            # Let's emit focusLost as that already triggers hiding in ImageGallery
                            # UPDATE: Emitting focusLost might have side effects. Let's just hide directly.
                            # self.focusLost.emit() # NO - Use specific signal or direct call if possible
                            self.requestHideSuggestions.emit() # Emit a dedicated signal

                            operator_found = True
                            break # Found the operator, no need to check others or tag boundaries
                    if operator_found:
                        break

                if operator_found:
                    self.requestHideSuggestions.emit() # Hide suggestions on operator click
                    return True

                # --- Existing Tag Segment Selection Logic (runs only if not on an operator) ---
                boundaries = self._find_tag_segment_boundaries(current_text, click_char_pos)
                if boundaries:
                    start_pos, end_pos = boundaries
                    if start_pos == end_pos: return False
                    segment_length = end_pos - start_pos
                    selected_text = current_text[start_pos:end_pos]
                    self.search_field.setSelection(start_pos, segment_length)
                    self.tagSegmentSelected.emit(selected_text)
                    return True
                else:
                     print("  Double-click boundary detection failed, allowing default.") # Debug
                # --- End Existing Logic ---

        # Return False to allow the event to be processed further if not handled
        return False

    def _emit_search_request(self):
        search_query = self.search_field.text().strip()
        self.searchRequested.emit(search_query)
        # --- Keep focus on search field after clicking button? Optional. ---
        # self.search_field.setFocus()

    # --- Public Methods / Slots ---

    def get_current_query(self) -> str:
        """Returns the current text in the search field."""
        return self.search_field.text()

    def get_cursor_position(self) -> int:
        """Returns the current cursor position in the search field."""
        return self.search_field.cursorPosition()

    def set_query_text(self, text: str):
        """Sets the text in the search field."""
        # Block signals temporarily to avoid triggering unwanted updates
        self.search_field.blockSignals(True)
        self.search_field.setText(text)
        self.search_field.blockSignals(False)
    
    def _find_tag_segment_boundaries(self, text: str, pos: int) -> Optional[tuple[int, int]]:
        """
        Finds the start and end indices of the tag segment containing the given position.
        Returns (start_pos, end_pos) or None if position is invalid.
        """
        if not (0 <= pos <= len(text)):
            return None

        # Delimiters and operators (same as in insert_suggestion)
        start_delimiters = {'[', ']'}
        end_delimiters = {'[', ']'}
        operators = {'AND', 'OR', 'NOT'}

        # Find the start of the current tag segment (scan backwards)
        start_pos = 0
        i = pos - 1 # Start looking just before the cursor/click position
        while i >= 0:
            char = text[i]
            if char in start_delimiters:
                start_pos = i + 1
                break
            if char.isspace():
                potential_op_end = i
                potential_op_start = potential_op_end
                while potential_op_start > 0 and not text[potential_op_start-1].isspace() and text[potential_op_start-1] not in '[]':
                    potential_op_start -= 1
                word = text[potential_op_start:potential_op_end]
                if word.upper() in operators:
                     is_bounded_before = (potential_op_start == 0 or text[potential_op_start-1].isspace() or text[potential_op_start-1] in '[]')
                     if is_bounded_before:
                         start_pos = i + 1
                         break
            i -= 1

        # Find the end of the current tag segment (scan forwards)
        end_pos = len(text)
        i = pos # Start looking from the cursor/click position
        while i < len(text):
            char = text[i]
            if char in end_delimiters:
                end_pos = i
                break
            if char.isspace():
                potential_op_start = i + 1
                potential_op_end = potential_op_start
                while potential_op_end < len(text) and not text[potential_op_end].isspace() and text[potential_op_end] not in '[]':
                    potential_op_end += 1
                word = text[potential_op_start:potential_op_end]
                if word.upper() in operators:
                     is_bounded_after = (potential_op_end == len(text) or text[potential_op_end].isspace() or text[potential_op_end] in '[]')
                     if is_bounded_after:
                         end_pos = i
                         break
            i += 1

        # Ensure start_pos is not after end_pos (can happen at boundaries)
        if start_pos > end_pos:
             # This might indicate clicking right on a delimiter, handle gracefully
             # Maybe return the position itself if we are exactly on a delimiter space?
             # For now, let's just return None or adjust. If click is ON space between tags, select nothing?
             # Let's try returning None for now, letting default handler work.
             return None


        return start_pos, end_pos

    def insert_suggestion(self, tag_to_insert: str):
        """Replaces the current tag segment at the cursor with the selected tag."""
        current_text = self.search_field.text()
        cursor_pos = self.search_field.cursorPosition()

        boundaries = self._find_tag_segment_boundaries(current_text, cursor_pos)
        if boundaries is None:
            print("insert_suggestion: Could not determine boundaries, insertion might be approximate.")
            # Fallback to simpler space-based logic? Or just insert at cursor?
            # For now, let's just log and potentially do nothing or simple insert.
            # Let's try inserting at the cursor as a fallback
            start_pos = cursor_pos
            end_pos = cursor_pos
            # Or maybe use the old simpler logic here?
            # Let's stick with the stricter boundary check for now. If None, do nothing.
            print("insert_suggestion: Aborting insertion due to unclear boundaries.")
            return

        start_pos, end_pos = boundaries

        print(f"Original text: '{current_text}'")
        print(f"Cursor pos: {cursor_pos}")
        print(f"Found segment boundaries: start={start_pos}, end={end_pos}")
        print(f"Segment to replace: '{current_text[start_pos:end_pos]}'")
        print(f"Inserting: '{tag_to_insert}'")

        new_text_parts = []
        new_text_parts.append(current_text[:start_pos])
        new_text_parts.append(tag_to_insert)

        needs_space_after = True
        if end_pos == len(current_text): needs_space_after = False
        elif end_pos < len(current_text):
             next_char = current_text[end_pos]
             if next_char.isspace() or next_char == ']': needs_space_after = False
        if needs_space_after: new_text_parts.append(" ")

        new_text_parts.append(current_text[end_pos:])
        new_text = "".join(new_text_parts)
        new_cursor_pos = start_pos + len(tag_to_insert)
        if needs_space_after: new_cursor_pos += 1

        print(f"Resulting text: '{new_text}'")
        print(f"New cursor pos: {new_cursor_pos}")

        self.search_field.blockSignals(True)
        self.search_field.setText(new_text)
        self.search_field.setCursorPosition(min(new_cursor_pos, len(new_text)))
        self.search_field.blockSignals(False)
        self.inputChanged.emit()