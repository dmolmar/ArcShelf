# utils/path_utils.py
import os
import math
from pathlib import Path
from typing import Optional

def human_readable_size(size_bytes: Optional[int]) -> str:
    """Converts size in bytes to human-readable string."""
    if size_bytes is None or size_bytes < 0:
        return "N/A"
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    try:
        i = int(math.floor(math.log(size_bytes, 1024)))
        # Clamp index to the range of size_name
        i = max(0, min(i, len(size_name) - 1))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"
    except (ValueError, OverflowError):
        # Handle potential math errors for very large numbers
        return f"{size_bytes} B"


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