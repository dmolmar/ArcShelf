import re
from typing import List, Optional

# --- Token Definition ---
class Token:
    """Represents a token identified during the lexing phase."""
    def __init__(self, type: str, value: str):
        self.type = type
        self.value = value

    def __repr__(self):
        return f"Token({self.type!r}, {self.value!r})"

# --- Abstract Syntax Tree (AST) Node Definitions ---
class ASTNode:
    """Base class for all AST nodes."""
    pass

class TagNode(ASTNode):
    """Represents a single tag in the query."""
    def __init__(self, tag: str):
        self.tag = tag

    def __repr__(self):
        return f"TagNode({self.tag!r})"

class AndNode(ASTNode):
    """Represents a logical AND operation between two nodes."""
    def __init__(self, left: ASTNode, right: ASTNode):
        self.left = left
        self.right = right

    def __repr__(self):
        return f"AndNode({self.left!r}, {self.right!r})"

class OrNode(ASTNode):
    """Represents a logical OR operation between two nodes."""
    def __init__(self, left: ASTNode, right: ASTNode):
        self.left = left
        self.right = right

    def __repr__(self):
        return f"OrNode({self.left!r}, {self.right!r})"

class NotNode(ASTNode):
    """Represents a logical NOT operation on a node."""
    def __init__(self, node: ASTNode):
        self.node = node

    def __repr__(self):
        return f"NotNode({self.node!r})"

class BracketNode(ASTNode):
    """Represents an expression enclosed in brackets for precedence."""
    def __init__(self, expression: ASTNode):
        self.expression = expression

    def __repr__(self):
        return f"BracketNode({self.expression!r})"

class AllImagesNode(ASTNode):
    """Represents a query for all images (e.g., an empty query string)."""
    # This node might be generated by the parser if the query is empty,
    # or handled separately by the evaluator logic.
    def __repr__(self):
        return "AllImagesNode()"

# --- Search Query Parser ---
class SearchQueryParser:
    """
    Parses a search query string into an Abstract Syntax Tree (AST).
    Treats AND, OR, NOT, [, ] as delimiters. Everything else between
    these delimiters is considered a single tag, allowing spaces without quotes.
    """
    def __init__(self):
        self.tokens: List[Token] = []
        self.current_token: Optional[Token] = None

        # --- NEW: Define delimiters and compile regex patterns ---
        self.delimiters = {
            'AND': r'\bAND\b',
            'OR': r'\bOR\b',
            'NOT': r'\bNOT\b',
            'LBRACKET': r'\[',
            'RBRACKET': r'\]',
        }
        # Compile regex for finding delimiters (case-insensitive for operators)
        self.delimiter_patterns = {
            kind: re.compile(pattern, re.IGNORECASE)
            for kind, pattern in self.delimiters.items()
        }
        # --- END NEW ---

    # --- NEW: Non-regex based tokenizer ---
    def tokenize(self, query: str) -> List[Token]:
        """
        Converts the input query string into a list of tokens based on
        AND, OR, NOT, [, ] as delimiters.
        """
        self.tokens = []
        pos = 0
        query_len = len(query)

        while pos < query_len:
            # --- 1. Skip leading whitespace ---
            start_pos = pos
            while start_pos < query_len and query[start_pos].isspace():
                start_pos += 1
            # If we reached the end after skipping whitespace, break
            if start_pos == query_len:
                break
            pos = start_pos # Update current position after skipping whitespace

            # --- 2. Find the *next* delimiter ---
            first_match_pos = query_len
            first_match_kind = None
            first_match_end = query_len

            # Search for the earliest occurrence of any delimiter from the current position
            for kind, pattern in self.delimiter_patterns.items():
                match = pattern.search(query, pos)
                if match and match.start() < first_match_pos:
                    first_match_pos = match.start()
                    first_match_kind = kind
                    first_match_end = match.end()

            # --- 3. Extract the tag (if any) before the delimiter ---
            # The tag text is from the current position up to the start of the found delimiter
            tag_text = query[pos:first_match_pos].strip() # Strip whitespace from the tag itself

            # If we found non-whitespace text before the delimiter (or before the end), it's a tag
            if tag_text:
                self.tokens.append(Token('TAG', tag_text))

            # --- 4. Add the delimiter token (if one was found) ---
            if first_match_kind:
                delimiter_value = query[first_match_pos:first_match_end]
                # Use uppercase for operators for consistency in the token type if desired,
                # though the kind already tells us what it is.
                self.tokens.append(Token(first_match_kind, delimiter_value))
                pos = first_match_end # Move position past the delimiter
            else:
                # No more delimiters found, loop will terminate as pos reaches query_len
                # The last tag (if any) was added in step 3.
                pos = query_len

        return self.tokens
    # --- END NEW ---

    # (Keep next_token method as is)
    def next_token(self):
        """Advances to the next token in the list."""
        if self.tokens:
            self.current_token = self.tokens.pop(0)
        else:
            self.current_token = None # End of tokens

    # (Keep parse method as is)
    def parse(self, query: str) -> ASTNode:
        """Parses the tokenized query into an AST."""
        if not query.strip():
             return AllImagesNode()

        self.tokenize(query) # Uses the NEW tokenize method
        if not self.tokens:
             return AllImagesNode()

        self.next_token()
        result = self.parse_expression()

        if self.current_token is not None:
            # Use the value of the unexpected token in the error message
            raise ValueError(f"Unexpected token at end of query: Token('{self.current_token.type}', '{self.current_token.value}')")


        return result

    # --- Recursive Descent Parsing Methods ---
    # (Keep parse_expression and parse_term as is)
    def parse_expression(self) -> ASTNode:
        node = self.parse_term()
        while self.current_token and self.current_token.type == 'OR':
            self.next_token()
            right_node = self.parse_term()
            node = OrNode(node, right_node)
        return node

    def parse_term(self) -> ASTNode:
        node = self.parse_factor()
        while self.current_token and self.current_token.type == 'AND':
            self.next_token()
            right_node = self.parse_factor()
            node = AndNode(node, right_node)
        return node

    # (Slightly simplify parse_factor as QUOTED_TAG is gone)
    def parse_factor(self) -> ASTNode:
        token = self.current_token

        if token is None:
             raise ValueError("Unexpected end of query, expected tag, NOT, or bracket.")

        if token.type == 'NOT':
            self.next_token()
            node = self.parse_factor()
            return NotNode(node)
        elif token.type == 'LBRACKET':
            self.next_token()
            node = self.parse_expression()
            if self.current_token and self.current_token.type == 'RBRACKET':
                self.next_token()
                # --- CHANGE: Return BracketNode for clarity/potential future use ---
                # Although currently the evaluator just processes the inner expression,
                # returning an explicit BracketNode is cleaner AST design.
                return BracketNode(node)
                # --- END CHANGE ---
            else:
                raise ValueError("Mismatched brackets: Expected ']'")
        # --- CHANGE: Only handle 'TAG' now ---
        elif token.type == 'TAG':
            tag_value = token.value # Value already stripped by tokenizer
            self.next_token()
            return TagNode(tag_value)
        # --- END CHANGE ---
        else:
            # Include token details in the error
            raise ValueError(f"Invalid syntax: Unexpected token Token('{token.type}', '{token.value}')")
