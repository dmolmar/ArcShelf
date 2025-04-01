import sqlite3
import os
import threading
import uuid
from pathlib import Path
from typing import List, Tuple, Optional, TYPE_CHECKING, Dict # Removed DefaultDict from here
from collections import defaultdict # Added defaultdict import
from PIL import Image

# Import the new normalization function
from utils.path_utils import normalize_path

# Use TYPE_CHECKING to avoid circular imports for type hints
if TYPE_CHECKING:
    from image_processing.thumbnail import ThumbnailCache
    from image_processing.tagger import ImageTaggerModel # Assuming ImageTaggerModel will be in tagger.py

# Import the data model
from .models import TagPrediction

class Database:
    def __init__(self, db_path: Path, thumbnail_cache: 'ThumbnailCache'):
        """
        Initializes the database manager.

        Args:
            db_path: Path to the SQLite database file.
            thumbnail_cache: An instance of the ThumbnailCache.
        """
        self.db_path = db_path
        self.thumbnail_cache = thumbnail_cache
        # Ensure the directory for the database exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Use a reentrant lock to allow the same thread to acquire the lock multiple times
        self.lock = threading.RLock()
        self._init_db()
        # print(f"Database initialized at: {self.db_path}") # Removed debug print

    def _init_db(self):
        """Initializes the database schema if it doesn't exist."""
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.executescript("""
                    CREATE TABLE IF NOT EXISTS tags (
                        id INTEGER PRIMARY KEY,
                        name TEXT UNIQUE NOT NULL, -- Added NOT NULL constraint
                        category TEXT
                    );
                    CREATE TABLE IF NOT EXISTS images (
                        id TEXT PRIMARY KEY,
                        path TEXT UNIQUE NOT NULL, -- Added NOT NULL constraint
                        rating TEXT,
                        file_size INTEGER,
                        modification_time REAL, -- Use REAL for potentially more precision
                        resolution TEXT -- e.g., "1920x1080"
                    );
                    CREATE TABLE IF NOT EXISTS image_tags (
                        image_id TEXT NOT NULL, -- Added NOT NULL constraint
                        tag_id INTEGER NOT NULL, -- Added NOT NULL constraint
                        confidence REAL,
                        FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE, -- Cascade deletes
                        FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE, -- Cascade deletes
                        PRIMARY KEY (image_id, tag_id)
                    );
                    """)
                    # Create indexes for performance
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name)")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_path ON images(path)")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_image_tags_image_id ON image_tags(image_id)")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_image_tags_tag_id ON image_tags(tag_id)")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_modification_time ON images(modification_time)")
                    # Consider adding indexes for rating, file_size, resolution if frequently searched/sorted
                    conn.commit()
            # print("Database schema initialized/verified.") # Removed debug print
        except sqlite3.Error as e:
            print(f"Database initialization error: {e}")
            raise

    def image_exists(self, path: str) -> bool:
        """Checks if an image with the given path exists in the database."""
        normalized_path = normalize_path(path)
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT 1 FROM images WHERE path = ?", (normalized_path,))
                    return cursor.fetchone() is not None
        except sqlite3.Error as e:
            print(f"Error checking if image exists ({normalized_path}): {e}")
            return False # Assume not exists on error

    def add_image(self, path: str, predictions: List[TagPrediction], model: 'ImageTaggerModel'):
        """Adds or updates an image and its tags in the database."""
        normalized_path = normalize_path(path)
        try:
            # Get file metadata safely
            if not os.path.exists(path):
                print(f"File not found, cannot add image: {path}")
                return
            current_mod_time = os.path.getmtime(path)
            current_file_size = os.path.getsize(path)
        except OSError as e:
            print(f"Error accessing file metadata for {path}: {e}")
            return

        # Get image resolution safely
        resolution = "unknown"
        try:
            with Image.open(path) as img:
                width, height = img.size
                resolution = f"{width}x{height}"
        except Exception as e:
            print(f"Error getting resolution for {path}: {e}")

        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    # --- MODIFICATION HERE: Added COLLATE NOCASE ---
                    cursor.execute("SELECT id, modification_time, file_size, resolution FROM images WHERE path = ? COLLATE NOCASE", (normalized_path,))
                    # --- END MODIFICATION ---
                    row = cursor.fetchone()

                    image_id = None
                    needs_retagging = False
                    needs_thumbnail_update = False
                    existing_rating = None # Store existing rating if found

                    if row:
                        image_id, stored_mod_time, stored_file_size, stored_resolution = row
                        # --- Get existing rating ---
                        cursor.execute("SELECT rating FROM images WHERE id = ?", (image_id,))
                        rating_row = cursor.fetchone()
                        existing_rating = rating_row[0] if rating_row else None
                        # --- End Get existing rating ---

                        # Check if file changed significantly or resolution was unknown/changed
                        # Using a tolerance for mod_time comparison
                        mod_time_changed = abs(current_mod_time - stored_mod_time) > 1 if stored_mod_time else True
                        size_changed = stored_file_size != current_file_size
                        resolution_changed = stored_resolution != resolution

                        if mod_time_changed or size_changed or resolution_changed:
                            print(f"Updating metadata for existing image: {normalized_path} (ModTime:{mod_time_changed}, Size:{size_changed}, Res:{resolution_changed})")
                            cursor.execute("UPDATE images SET file_size = ?, modification_time = ?, resolution = ? WHERE id = ?",
                                           (current_file_size, current_mod_time, resolution, image_id))
                            needs_retagging = True # Re-tag if file changed (metadata or content)
                            needs_thumbnail_update = True
                        # Check if thumbnail is missing/invalid even if file metadata matches
                        elif not self.thumbnail_cache.is_thumbnail_valid(image_id):
                             needs_thumbnail_update = True

                    else:
                        # Image is new (or wasn't found due to previous case-sensitivity issue)
                        print(f"Adding new image: {normalized_path}")
                        rating = model.determine_rating(predictions) # Determine rating for new image
                        image_id = str(uuid.uuid4())
                        cursor.execute("INSERT INTO images (id, path, rating, file_size, modification_time, resolution) VALUES (?, ?, ?, ?, ?, ?)",
                                       (image_id, normalized_path, rating, current_file_size, current_mod_time, resolution))
                        needs_retagging = True # Tag new images
                        needs_thumbnail_update = True

                    # Perform retagging if needed
                    if needs_retagging and image_id:
                        print(f"Updating tags for image: {image_id}")
                        determined_rating = model.determine_rating(predictions)
                        # Update rating in DB if it changed or was newly determined
                        if existing_rating != determined_rating: # Check against stored or initial None
                             cursor.execute("UPDATE images SET rating = ? WHERE id = ?", (determined_rating, image_id))

                        # Delete old tags before adding new ones
                        cursor.execute("DELETE FROM image_tags WHERE image_id = ?", (image_id,))

                        # Filter predictions... (rest of tagging logic remains the same)
                        filtered_predictions = [pred for pred in predictions if pred.category.lower() != "rating" or pred.tag.lower() == determined_rating.lower()]

                        tag_ids_map = {} # Cache tag IDs to reduce queries

                        # Get existing tags first to minimize inserts
                        tag_names_to_check = {pred.tag for pred in filtered_predictions}
                        if tag_names_to_check:
                            placeholders = ','.join('?' for _ in tag_names_to_check)
                            cursor.execute(f"SELECT id, name FROM tags WHERE name IN ({placeholders})", list(tag_names_to_check))
                            for tag_id, tag_name in cursor.fetchall():
                                tag_ids_map[tag_name] = tag_id

                        # Insert new tags and prepare image_tags data
                        image_tags_to_insert = []
                        for pred in filtered_predictions:
                            tag_id = tag_ids_map.get(pred.tag)
                            if tag_id is None:
                                # Attempt to insert the tag if it wasn't found in our initial bulk fetch
                                cursor.execute("INSERT OR IGNORE INTO tags (name, category) VALUES (?, ?)", (pred.tag, pred.category))
                                # Fetch the ID again, whether it was just inserted or ignored (already existed)
                                cursor.execute("SELECT id FROM tags WHERE name = ?", (pred.tag,))
                                tag_id_row = cursor.fetchone()
                                if tag_id_row:
                                    tag_id = tag_id_row[0]
                                    tag_ids_map[pred.tag] = tag_id # Cache it for potential future use in this loop
                                else:
                                    # This case should be rare if INSERT OR IGNORE works, but handles potential issues
                                    print(f"Warning: Could not retrieve tag_id for '{pred.tag}' even after INSERT OR IGNORE attempt.")
                                    continue # Skip this tag prediction if we can't get an ID

                            # Only append if we successfully got a tag_id
                            if tag_id is not None:
                                image_tags_to_insert.append((image_id, tag_id, pred.confidence))

                        if image_tags_to_insert:
                            cursor.executemany("INSERT INTO image_tags (image_id, tag_id, confidence) VALUES (?, ?, ?)", image_tags_to_insert)


                    # Update thumbnail if needed
                    if needs_thumbnail_update and image_id:
                        self.thumbnail_cache.update_thumbnail(path, image_id)

                    conn.commit() # Commit transaction

        except sqlite3.Error as e:
            print(f"Database error adding/updating image {normalized_path}: {e}")
        except Exception as e:
            print(f"Unexpected error adding/updating image {normalized_path}: {e}")


    def delete_images_in_directory(self, directory: str):
        """Deletes all images from the database that reside within a given directory."""
        directory_path = normalize_path(directory)
        # Ensure directory path ends with '/' for accurate LIKE matching
        if not directory_path.endswith('/'):
            directory_path += '/'
        print(f"Attempting to delete images in directory: {directory_path}")
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    # Find images directly within the directory or in subdirectories
                    cursor.execute("SELECT id FROM images WHERE path LIKE ?", (f"{directory_path}%",))
                    image_ids_to_delete = [row[0] for row in cursor.fetchall()]

                    if not image_ids_to_delete:
                        print(f"No images found in database for directory: {directory_path}")
                        return

                    print(f"Found {len(image_ids_to_delete)} images to delete from directory: {directory_path}")

                    # Delete from images table (CASCADE should handle image_tags)
                    placeholders = ','.join('?' for _ in image_ids_to_delete)
                    cursor.execute(f"DELETE FROM images WHERE id IN ({placeholders})", image_ids_to_delete)

                    # Optionally, explicitly remove orphaned tags immediately
                    self.remove_orphaned_tags(conn)

                    conn.commit()

                    # Delete associated thumbnails
                    for image_id in image_ids_to_delete:
                        self.thumbnail_cache.delete_thumbnail(image_id)

                    print(f"Successfully deleted {len(image_ids_to_delete)} images and associated data for directory: {directory_path}")

        except sqlite3.Error as e:
            print(f"Database error deleting images in directory {directory_path}: {e}")
        except Exception as e:
            print(f"Unexpected error deleting images in directory {directory_path}: {e}")


    def cleanup_database(self):
        """Removes records for images that no longer exist on the filesystem."""
        # print("Starting database cleanup...") # Removed debug print
        image_ids_to_delete = []
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id, path FROM images")
                    rows = cursor.fetchall()
                    # print(f"Checking {len(rows)} images for existence...") # Removed debug print
                    for image_id, db_path in rows:
                        # Use BASE_DIR from config if paths are relative, otherwise assume absolute
                        # Assuming paths stored are absolute or resolvable from CWD
                        # Normalize the path read from DB before checking existence
                        normalized_db_path = normalize_path(db_path)
                        # Check existence using the original path string for Path object creation,
                        # as normalize_path might return an empty string for invalid inputs.
                        # The core check remains if the file pointed to by the DB entry exists.
                        if not db_path or not Path(db_path).is_file():
                            # Log the original path from DB and the normalized version if different for debugging
                            log_path = db_path if db_path else "<NULL>"
                            log_normalized = normalized_db_path if normalized_db_path != log_path else ""
                            print(f"Image file not found, marking for deletion: {log_path} {f'(Normalized: {log_normalized})' if log_normalized else ''} (ID: {image_id})")
                            image_ids_to_delete.append(image_id)

                    if not image_ids_to_delete:
                        print("No orphaned images found.")
                        return

                    print(f"Found {len(image_ids_to_delete)} orphaned images to delete.")

                    # Delete from images table (CASCADE should handle image_tags)
                    placeholders = ','.join('?' for _ in image_ids_to_delete)
                    cursor.execute(f"DELETE FROM images WHERE id IN ({placeholders})", image_ids_to_delete)

                    # Remove orphaned tags
                    deleted_tag_count = self.remove_orphaned_tags(conn)
                    print(f"Removed {deleted_tag_count} orphaned tags.")

                    conn.commit()

                    # Delete associated thumbnails
                    for image_id in image_ids_to_delete:
                        self.thumbnail_cache.delete_thumbnail(image_id)

                    print(f"Successfully deleted {len(image_ids_to_delete)} orphaned images and associated data.")

        except sqlite3.Error as e:
            print(f"Database error during cleanup: {e}")
        except Exception as e:
            print(f"Unexpected error during cleanup: {e}")


    def vacuum_database(self):
        """
        Vacuums the database to potentially reduce file size and optimize structure.
        This operation can be slow and locks the database.
        """
        # print("Starting database VACUUM operation...") # Removed debug print
        try:
            # VACUUM needs exclusive access, run outside the main lock if possible,
            # or ensure no other operations are happening.
            # The 'with sqlite3.connect...' handles connection closing.
            with sqlite3.connect(self.db_path) as conn:
                 # Set a timeout in case it takes too long
                conn.execute("PRAGMA busy_timeout = 60000") # 60 seconds
                conn.execute("VACUUM")
            print("Database vacuumed successfully.")
        except sqlite3.Error as e:
            print(f"Database error during VACUUM: {e}")


    def remove_orphaned_tags(self, conn) -> int:
        """Removes tags that are no longer associated with any image. Returns count of deleted tags."""
        try:
            cursor = conn.cursor()
            # Use LEFT JOIN to find tags not present in image_tags
            cursor.execute("""
                DELETE FROM tags
                WHERE id IN (
                    SELECT t.id
                    FROM tags t
                    LEFT JOIN image_tags it ON t.id = it.tag_id
                    WHERE it.tag_id IS NULL
                )
            """)
            deleted_count = cursor.rowcount
            # No commit here, assumes called within a transaction
            return deleted_count
        except sqlite3.Error as e:
            print(f"Error removing orphaned tags: {e}")
            return 0


    def get_image_info_by_path(self, path: str) -> Tuple[Optional[str], List[TagPrediction]]:
        """Retrieves the rating and tags for a given image path."""
        normalized_path = normalize_path(path)
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    # Read-only access, potentially faster
                    conn.execute("PRAGMA query_only = ON")
                    cursor = conn.cursor()
                    # Use COLLATE NOCASE for case-insensitive path matching
                    cursor.execute("SELECT id, rating FROM images WHERE path = ? COLLATE NOCASE", (normalized_path,))
                    row = cursor.fetchone()
                    if row:
                        image_id, rating = row
                        cursor.execute("""
                            SELECT t.name, t.category, it.confidence
                            FROM image_tags it
                            JOIN tags t ON it.tag_id = t.id
                            WHERE it.image_id = ?
                            ORDER BY it.confidence DESC -- Optionally order tags
                        """, (image_id,))
                        tags = [TagPrediction(tag, confidence, category) for tag, category, confidence in cursor.fetchall()]
                        return rating, tags
                    else:
                        return None, []
        except sqlite3.Error as e:
            print(f"Database error getting image info for {normalized_path}: {e}")
            return None, []

    def get_matching_tags_for_directories(self, desired_dirs: List[str], undesired_dirs: List[str],
                                          desired_tags: List[str], undesired_tags: List[str],
                                          search_term: str, limit: Optional[int] = 100) -> List[Tuple[str, int]]:
        """
        Finds tags matching a search term prefix within images filtered by directories and tags.
        If search_term is empty, returns top tags by count.

        Args:
            desired_dirs: List of directory paths where images MUST be.
            undesired_dirs: List of directory paths where images MUST NOT be.
            desired_tags: List of tags that images MUST have (AND logic).
            undesired_tags: List of tags that images MUST NOT have.
            search_term: The prefix term to filter tag names by (case-insensitive LIKE 'term%').
                         If empty, returns top tags.
            limit: Max number of tags to return (especially useful when search_term is empty).

        Returns:
            A list of tuples (tag_name, count), sorted by count descending.
        """
        print(f"Database: get_matching_tags_for_directories called with search_term='{search_term}'")

        if not desired_dirs:
            print("Database: No desired directories selected, returning empty list for tag matching.")
            return []

        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("PRAGMA query_only = ON")
                    cursor = conn.cursor()

                    # --- Build subquery to filter image IDs ---
                    image_id_subquery = "SELECT i.id FROM images i"
                    image_conditions = []
                    image_params = []

                    # 1. Desired Directories (OR logic between directories)
                    dir_conditions = []
                    for d_dir in desired_dirs:
                        norm_dir = normalize_path(d_dir)
                        if not norm_dir.endswith('/'): norm_dir += '/'
                        dir_conditions.append("i.path LIKE ?")
                        image_params.append(f"{norm_dir}%")
                    if dir_conditions:
                         image_conditions.append("(" + " OR ".join(dir_conditions) + ")")

                    # 2. Undesired Directories (AND NOT logic)
                    for u_dir in undesired_dirs:
                        norm_dir = normalize_path(u_dir)
                        if not norm_dir.endswith('/'): norm_dir += '/'
                        image_conditions.append("i.path NOT LIKE ?")
                        image_params.append(f"{norm_dir}%")

                    # 3. Desired Tags (AND logic - image must have ALL desired tags)
                    if desired_tags:
                        image_id_subquery += " JOIN image_tags it_d ON i.id = it_d.image_id JOIN tags t_d ON it_d.tag_id = t_d.id"
                        placeholders = ','.join('?' * len(desired_tags))
                        image_conditions.append(f"""
                            i.id IN (
                                SELECT it_sub.image_id
                                FROM image_tags it_sub JOIN tags t_sub ON it_sub.tag_id = t_sub.id
                                WHERE t_sub.name IN ({placeholders})
                                GROUP BY it_sub.image_id
                                HAVING COUNT(DISTINCT t_sub.name) = ?
                            )
                        """)
                        image_params.extend(desired_tags)
                        image_params.append(len(desired_tags))

                    # 4. Undesired Tags (AND NOT logic - image must have NONE of the undesired tags)
                    if undesired_tags:
                        placeholders = ','.join('?' * len(undesired_tags))
                        image_conditions.append(f"""
                            i.id NOT IN (
                                SELECT DISTINCT it_sub.image_id
                                FROM image_tags it_sub JOIN tags t_sub ON it_sub.tag_id = t_sub.id
                                WHERE t_sub.name IN ({placeholders})
                            )
                        """)
                        image_params.extend(undesired_tags)

                    # Combine image conditions
                    if image_conditions:
                        image_id_subquery += " WHERE " + " AND ".join(image_conditions)

                    # --- Build main query to get tag counts ---
                    final_params = list(image_params) # Copy params used for subquery

                     # --- MODIFICATION: Handle search_term condition ---
                    search_condition = ""
                    if search_term:
                        search_condition = "AND t.name LIKE ? COLLATE NOCASE" # Prefix search
                        final_params.append(f'{search_term}%') # Append % for prefix match
                    # If search_term is empty, no t.name condition is added, showing all tags
                    # --- END MODIFICATION ---

                    # Add LIMIT clause
                    limit_clause = f"LIMIT {int(limit)}" if limit is not None and limit > 0 else ""

                    tag_query = f"""
                    SELECT t.name, COUNT(DISTINCT it.image_id) as count
                    FROM tags t
                    JOIN image_tags it ON t.id = it.tag_id
                    WHERE it.image_id IN ({image_id_subquery})
                    {search_condition}
                    GROUP BY t.name
                    ORDER BY count DESC, t.name ASC
                    {limit_clause}
                    """

                    # print(f"Database: Executing SQL query: {tag_query}")
                    # print(f"Database: Query parameters: {final_params}")

                    cursor.execute(tag_query, final_params)
                    result = cursor.fetchall()
                    print(f"Database: Tag matching query returned {len(result)} tags.")
                    return result

        except sqlite3.Error as e:
            print(f"Database error getting matching tags: {e}")
            return []
        except Exception as e:
            print(f"Unexpected error getting matching tags: {e}")
            return []

    def get_image_id_from_path(self, path: str) -> Optional[str]:
        """Retrieves the UUID for a given image path."""
        normalized_path = normalize_path(path)
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("PRAGMA query_only = ON")
                    cursor = conn.cursor()
                    # Use COLLATE NOCASE for case-insensitive path matching
                    cursor.execute("SELECT id FROM images WHERE path = ? COLLATE NOCASE", (normalized_path,))
                    row = cursor.fetchone()
                    return row[0] if row else None
        except sqlite3.Error as e:
            print(f"Database error getting image ID for {normalized_path}: {e}")

    def get_resolutions_for_paths(self, paths: List[str]) -> dict[str, Optional[str]]:
        """
        Retrieves the resolution string for a list of image paths efficiently
        using a temporary table and JOIN.
        """
        if not paths:
            return {}

        # Initialize results with None for all requested original paths
        results = {path: None for path in paths}
        # Create a mapping from normalized path back to a list of original paths
        normalized_to_originals: Dict[str, List[str]] = defaultdict(list)
        unique_normalized_paths: set[str] = set()
        for p in paths:
            if p: # Ensure path is not empty
                normalized = normalize_path(p)
                if normalized: # Ensure normalization didn't result in empty string
                    unique_normalized_paths.add(normalized)
                    normalized_to_originals[normalized].append(p)

        if not unique_normalized_paths:
            print("DEBUG: get_resolutions_for_paths - No valid normalized paths to query.")
            return results # Return dict with Nones

        db_resolutions: Dict[str, Optional[str]] = {} # {normalized_path_from_db: resolution}
        try:
            with self.lock:
                # Use a single connection for the temporary table lifecycle
                with sqlite3.connect(self.db_path, isolation_level=None) as conn: # Autocommit mode for temp table
                    cursor = conn.cursor()
                    try:
                        # Create a temporary table
                        cursor.execute("CREATE TEMP TABLE temp_paths_to_query (path TEXT PRIMARY KEY)")

                        # Insert normalized paths into the temporary table
                        # executemany expects a list of tuples
                        paths_to_insert = [(p,) for p in unique_normalized_paths]
                        cursor.executemany("INSERT INTO temp_paths_to_query (path) VALUES (?)", paths_to_insert)

                        # Query by joining images with the temporary table
                        # Use COLLATE NOCASE on the JOIN condition
                        query = """
                            SELECT i.path, i.resolution
                            FROM images i
                            JOIN temp_paths_to_query t ON i.path = t.path COLLATE NOCASE
                        """
                        cursor.execute(query)
                        fetched_rows = cursor.fetchall()

                    finally:
                        # Ensure temporary table is dropped even if errors occur
                        try:
                            cursor.execute("DROP TABLE temp_paths_to_query")
                        except sqlite3.Error as drop_err:
                            # Log error if dropping fails, but don't raise over original error
                            print(f"Warning: Could not drop temporary table temp_paths_to_query: {drop_err}")

            # Populate the lookup dictionary using normalized paths from the DB results
            for db_path, resolution in fetched_rows:
                if db_path: # Ensure path from DB is not null/empty
                    db_resolutions[normalize_path(db_path)] = resolution # Store using normalized key

            print(f"DEBUG: get_resolutions_for_paths - Fetched {len(db_resolutions)} resolutions for {len(unique_normalized_paths)} unique requested paths using TEMP TABLE.")

        except sqlite3.Error as e:
            print(f"Database error fetching specific resolutions using TEMP TABLE: {e}")
            # Return dict with Nones if DB fetch fails
            return results
        except Exception as e:
            print(f"Unexpected error fetching specific resolutions using TEMP TABLE: {e}")
            return results

        # Map the fetched resolutions back to the original input paths
        missing_count = 0
        for normalized_key, original_paths_list in normalized_to_originals.items():
            resolution = db_resolutions.get(normalized_key) # Lookup using normalized key
            if resolution is None:
                 # This normalized path was queried but not found in the DB results
                 print(f"DEBUG: get_resolutions_for_paths - Normalized path '{normalized_key}' not found in DB results (TEMP TABLE method).")
                 missing_count += 1
            # Assign the found resolution (or None) to all original paths that normalized to this key
            for original_path in original_paths_list:
                results[original_path] = resolution

        if missing_count > 0:
            print(f"DEBUG: get_resolutions_for_paths - {missing_count} queried normalized paths were not found in the DB (TEMP TABLE method).")

        return results

    def get_image_ids_in_directory(self, directory: str) -> List[str]:
        """Retrieves all image UUIDs within a given directory (recursive)."""
        directory_path = normalize_path(directory)
        if not directory_path.endswith('/'):
            directory_path += '/'
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("PRAGMA query_only = ON")
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM images WHERE path LIKE ?", (f"{directory_path}%",))
                    return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"Database error getting image IDs in directory {directory_path}: {e}")
            return []