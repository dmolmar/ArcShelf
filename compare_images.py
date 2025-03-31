import sqlite3
import os
from pathlib import Path
import sys
from collections import Counter # Make sure Counter is imported

# Import the new normalization function
from utils.path_utils import normalize_path

# Add project root to sys.path to import config
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

try:
    from config import DB_PATH, SUPPORTED_FORMATS
except ImportError:
    print("Error: Could not import DB_PATH and SUPPORTED_FORMATS from config.py.")
    print("Ensure compare_images.py is in the project root directory.")
    # Define fallbacks if config import fails
    DB_PATH = project_root / 'data' / 'images.db'
    SUPPORTED_FORMATS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff', '.tif')
    print(f"Using fallback DB_PATH: {DB_PATH}")
    print(f"Using fallback SUPPORTED_FORMATS: {SUPPORTED_FORMATS}")


# --- Configuration ---
# Use the absolute path provided by the user
IMAGE_DIR = Path(r"C:\Users\molma\Pictures\Arcueid")
# --- End Configuration ---
def get_database_image_count(db_path: Path) -> int:
    """Gets the total number of records in the images table."""
    count = 0
    if not db_path.exists():
        print(f"Error: Database file not found at {db_path}")
        return count
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM images")
            result = cursor.fetchone()
            if result:
                count = result[0]
    except sqlite3.Error as e:
        print(f"Database error getting image count: {e}")
    except Exception as e:
        print(f"Unexpected error getting image count: {e}")
    return count

def get_unique_database_image_paths(db_path: Path) -> set[str]:
    """Fetches all unique normalized image paths from the database."""
    db_paths = set()
    if not db_path.exists():
        print(f"Error: Database file not found at {db_path}")
        return db_paths
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT path FROM images")
            rows = cursor.fetchall()
            for row in rows:
                if row and row[0]:
                    db_paths.add(normalize_path(row[0]))
                else:
                    print("Warning: Found NULL or empty path in database.")
    except sqlite3.Error as e:
        print(f"Database error reading image paths: {e}")
    except Exception as e:
        print(f"Unexpected error reading database: {e}")
    return db_paths

def get_filesystem_image_paths_non_recursive(image_dir: Path, supported_formats: tuple) -> set[str]:
    """Finds all supported image files ONLY in the top level of a directory."""
    fs_paths = set()
    if not image_dir.is_dir():
        print(f"Error: Image directory not found or is not a directory: {image_dir}")
        return fs_paths

    print(f"Scanning directory (non-recursive): {image_dir}...")
    try:
        for item_name in os.listdir(image_dir):
            item_path = image_dir / item_name
            # Check if it's a file and has a supported extension
            if item_path.is_file() and item_name.lower().endswith(supported_formats):
                 fs_paths.add(normalize_path(str(item_path)))
    except OSError as e:
        print(f"Error scanning directory {image_dir} (non-recursive): {e}")
    except Exception as e:
        print(f"Unexpected error scanning directory (non-recursive): {e}")

    print(f"Found {len(fs_paths)} supported image files in the top-level directory.")
    return fs_paths


def compare_paths(db_paths: set[str], fs_paths: set[str], fs_desc: str):
    """Compares the two sets of paths and prints the differences."""
    print(f"\n--- Database vs Filesystem ({fs_desc}) Comparison Results ---")

    in_db_not_fs = db_paths - fs_paths
    in_fs_not_db = fs_paths - db_paths

    if not in_db_not_fs:
        print(f"\n[OK] All images in the database set were found in the {fs_desc} filesystem scan.")
    else:
        print(f"\n[!] {len(in_db_not_fs)} images found in DATABASE set but NOT in the {fs_desc} filesystem scan of '{IMAGE_DIR}':")
        if len(in_db_not_fs) < 50:
            for i, path in enumerate(sorted(list(in_db_not_fs))):
                print(f"  {i+1}. {path}")
        else:
             print(f"  (List truncated - {len(in_db_not_fs)} entries)")


    if not in_fs_not_db:
        print(f"\n[OK] All images found in the {fs_desc} filesystem scan of '{IMAGE_DIR}' were found in the database set.")
    else:
        print(f"\n[!] {len(in_fs_not_db)} images found in the {fs_desc} filesystem scan of '{IMAGE_DIR}' but NOT in the database set:")
        if len(in_fs_not_db) < 50:
             for i, path in enumerate(sorted(list(in_fs_not_db))):
                 print(f"  {i+1}. {path}")
        else:
             print(f"  (List truncated - {len(in_fs_not_db)} entries)")


    if not in_db_not_fs and not in_fs_not_db:
        print(f"\n--- Summary (DB vs {fs_desc} FS): Database set and {fs_desc} directory contents match! ---")
    else:
        print(f"\n--- Summary (DB vs {fs_desc} FS): Differences found. ---")


def find_duplicate_db_paths(db_path: Path):
    """Checks for duplicate path entries within the database using normalized paths."""
    print("\n--- Database Duplicate Path Check (Using Normalized Paths) ---")
    duplicates_found = False
    if not db_path.exists():
        print(f"Error: Database file not found at {db_path}")
        return

    normalized_paths_list = []
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT path FROM images WHERE path IS NOT NULL AND path != ''")
            rows = cursor.fetchall()
            for row in rows:
                # Normalize each path using the same function used for comparison
                normalized_paths_list.append(normalize_path(row[0]))

        # Use Counter to find duplicates in the normalized list
        path_counts = Counter(normalized_paths_list)
        duplicate_paths = {path: count for path, count in path_counts.items() if count > 1}

        if not duplicate_paths:
            print("[OK] No duplicate normalized image paths found within the database.")
        else:
            duplicates_found = True
            print(f"[!] {len(duplicate_paths)} duplicate normalized path entries found in the database:")
            i = 0
            for path, count in sorted(duplicate_paths.items()):
                i += 1
                print(f"  {i}. Path: {path} (Appears {count} times)")

    except sqlite3.Error as e:
        print(f"Database error checking for duplicates: {e}")
    except Exception as e:
        print(f"Unexpected error checking for duplicates: {e}")
    return duplicates_found


if __name__ == "__main__":
    print("--- Starting Image Comparison Script ---")
    print(f"Database Path: {DB_PATH}")
    print(f"Image Directory: {IMAGE_DIR}")
    print(f"Supported Formats: {SUPPORTED_FORMATS}")

    # --- Get Counts ---
    db_count = get_database_image_count(DB_PATH)
    print(f"\nTotal records found in database table 'images': {db_count}")

    filesystem_paths_non_recursive = get_filesystem_image_paths_non_recursive(IMAGE_DIR, SUPPORTED_FORMATS)
    fs_non_recursive_count = len(filesystem_paths_non_recursive)
    # Note: fs_non_recursive_count already printed inside the function

    # --- Compare DB vs Non-Recursive FS ---
    database_paths_set = get_unique_database_image_paths(DB_PATH)
    if database_paths_set or filesystem_paths_non_recursive:
        compare_paths(database_paths_set, filesystem_paths_non_recursive, "Non-Recursive")
    else:
        print("\nDB vs Non-Recursive FS comparison aborted due to errors fetching paths.")

    # --- Check for Duplicates within Database (Revised Method) ---
    find_duplicate_db_paths(DB_PATH)

    print("\n--- Script Finished ---")