# utils/path_utils.py
import os
from pathlib import Path

def normalize_path(path_str: str) -> str:
    """
    Normalizes a path string to a consistent format:
    - Resolves to an absolute path.
    - Converts entirely to lowercase.
    - Uses forward slashes ('/').

    Args:
        path_str: The input path string.

    Returns:
        The normalized path string.
    """
    if not path_str:
        return "" # Return empty string if input is empty

    try:
        # Resolve to absolute path first to handle '..' etc.
        # Use Path for robust handling.
        normalized_path = Path(path_str).resolve()
    except OSError as e:
        # Handle cases where the path might not exist or is invalid during resolution
        # Fallback to abspath which might handle some cases differently
        print(f"Warning: Could not resolve path '{path_str}' during normalization. Using os.path.abspath. Error: {e}")
        try:
            normalized_path_str = os.path.abspath(path_str)
        except Exception as abs_e:
            # If abspath also fails, return the original path lowercased with forward slashes as a last resort
            print(f"Warning: os.path.abspath also failed for '{path_str}'. Returning basic normalization. Error: {abs_e}")
            return str(path_str).replace(os.sep, '/').lower()
        # Convert the result of abspath back to Path object
        normalized_path = Path(normalized_path_str)

    except Exception as e:
         # Catch any other unexpected errors during Path resolution
         print(f"Unexpected error resolving path '{path_str}'. Using basic normalization. Error: {e}")
         # Fallback to basic normalization
         return str(path_str).replace(os.sep, '/').lower()

    # Convert to string, replace slashes, and convert to lowercase
    return normalized_path.as_posix().lower()