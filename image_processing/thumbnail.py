import os
import threading
from pathlib import Path
from typing import Optional, Dict
from PIL import Image, UnidentifiedImageError
from PyQt6.QtGui import QImage

# Define a fixed height for thumbnails or make it configurable
THUMBNAIL_HEIGHT = 400
# Define cache settings
MEMORY_CACHE_MAXSIZE = 100 # Increased memory cache size
DISK_CACHE_MAXSIZE = 5000 # Limit disk cache tracking if needed, though less critical than memory
WEBP_QUALITY = 85 # Quality for saved WEBP thumbnails

class ThumbnailCache:
    def __init__(self, cache_dir: Path):
        """
        Initializes the thumbnail cache.

        Args:
            cache_dir: The directory to store thumbnail files.
        """
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Memory cache stores loaded QImage objects
        self.memory_cache: Dict[str, QImage] = {}
        # Disk cache can track existence or store loaded QImages (similar to memory cache but potentially larger/persistent)
        # For simplicity, let's use it like memory cache but maybe with different eviction?
        # Or just use it to check existence quickly? Let's keep it simple for now.
        # self.disk_cache: Dict[str, QImage] = {} # Alternative: track loaded images from disk
        self.cache_lock = threading.Lock()
        print(f"Thumbnail cache initialized at: {self.cache_dir}")
        print(f"Memory cache max size: {MEMORY_CACHE_MAXSIZE}")

    def _get_cache_path(self, image_id: str) -> Path:
        """Gets the expected path for a thumbnail file."""
        return self.cache_dir / f"{image_id}.webp"

    def is_thumbnail_valid(self, image_id: str) -> bool:
        """Checks if a valid thumbnail file exists on disk for the given image ID."""
        cache_path = self._get_cache_path(image_id)
        # A simple check for file existence might be sufficient if we trust the files aren't corrupted.
        # For more robustness, could try loading it here, but get_thumbnail will do that anyway.
        return cache_path.is_file() and cache_path.stat().st_size > 0 # Check if file exists and is not empty

    def update_thumbnail(self, image_path: str, image_id: str):
        """
        Creates or updates the thumbnail for the given image path and ID.
        Resizes proportionally to a fixed height.
        """
        try:
            with Image.open(image_path) as img:
                # Calculate new width proportionally based on THUMBNAIL_HEIGHT
                width, height = img.size
                if height == 0: # Avoid division by zero
                    print(f"Warning: Image has zero height, cannot create thumbnail for {image_path}")
                    return
                new_height = THUMBNAIL_HEIGHT
                new_width = int(width * (new_height / height))
                if new_width == 0: new_width = 1 # Ensure width is at least 1

                # Use ANTIALIAS for better quality resizing
                thumbnail_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS) # High-quality downsampling
                self.store_thumbnail(image_id, thumbnail_img)
                print(f"Thumbnail updated for {image_id}")

        except FileNotFoundError:
             print(f"Error: Original image not found, cannot create thumbnail: {image_path}")
        except UnidentifiedImageError:
             print(f"Error: Cannot identify image file (possibly corrupt or unsupported format): {image_path}")
        except Exception as e:
            print(f"Error creating thumbnail for {image_path} (ID: {image_id}): {e}")

    def get_thumbnail(self, image_id: str) -> Optional[QImage]:
        """
        Retrieves a thumbnail QImage, checking memory cache first, then disk.
        Loads from disk and caches to memory if found.
        """
        with self.cache_lock:
            # 1. Check memory cache
            if image_id in self.memory_cache:
                # print(f"Cache hit (memory): {image_id}")
                return self.memory_cache[image_id]

            # 2. Check disk and load if exists
            cache_path = self._get_cache_path(image_id)
            if cache_path.is_file():
                try:
                    # print(f"Cache hit (disk), loading: {image_id}")
                    thumbnail = QImage(str(cache_path))
                    if not thumbnail.isNull():
                        # Add to memory cache (with eviction)
                        if len(self.memory_cache) >= MEMORY_CACHE_MAXSIZE:
                            # Simple FIFO eviction: remove the oldest item
                            oldest_key = next(iter(self.memory_cache))
                            del self.memory_cache[oldest_key]
                            # print(f"Memory cache full, evicted: {oldest_key}")
                        self.memory_cache[image_id] = thumbnail
                        return thumbnail
                    else:
                        # File exists but couldn't be loaded as QImage (corrupt?)
                        print(f"Warning: Thumbnail file exists but is invalid: {cache_path}")
                        # Optionally delete the corrupt file
                        try:
                            cache_path.unlink()
                        except OSError as e:
                            print(f"Error deleting invalid thumbnail {cache_path}: {e}")
                except Exception as e:
                    print(f"Error loading thumbnail from disk {cache_path}: {e}")

        # 3. Not found in cache or on disk
        # print(f"Cache miss: {image_id}")
        return None

    def store_thumbnail(self, image_id: str, thumbnail_img: Image.Image):
        """Saves the thumbnail (Pillow Image) to disk and updates caches."""
        cache_path = self._get_cache_path(image_id)
        try:
            # Save using Pillow
            thumbnail_img.save(str(cache_path), "WEBP", quality=WEBP_QUALITY)

            # Update memory cache immediately after saving
            # Load the saved file back as QImage to ensure consistency
            q_thumbnail = QImage(str(cache_path))
            if not q_thumbnail.isNull():
                 with self.cache_lock:
                    if len(self.memory_cache) >= MEMORY_CACHE_MAXSIZE:
                        oldest_key = next(iter(self.memory_cache))
                        del self.memory_cache[oldest_key]
                    self.memory_cache[image_id] = q_thumbnail
            else:
                 print(f"Warning: Could not load newly saved thumbnail as QImage: {cache_path}")


        except Exception as e:
            print(f"Error saving thumbnail to {cache_path}: {e}")

    def delete_thumbnail(self, image_id: str):
        """Deletes a thumbnail file from disk and removes it from caches."""
        cache_path = self._get_cache_path(image_id)
        deleted_from_disk = False
        try:
            if cache_path.is_file():
                cache_path.unlink() # Delete the thumbnail file from disk
                deleted_from_disk = True
                # print(f"Deleted thumbnail from disk: {cache_path}")
        except OSError as e:
            print(f"Error deleting thumbnail file {cache_path}: {e}")

        # Remove from memory cache regardless of disk deletion success
        with self.cache_lock:
            if image_id in self.memory_cache:
                del self.memory_cache[image_id]
                # print(f"Removed thumbnail from memory cache: {image_id}")

        # Optionally log if deletion happened
        # if deleted_from_disk:
        #     print(f"Thumbnail deleted for {image_id}")

    def clear_memory_cache(self):
        """Clears the in-memory thumbnail cache."""
        with self.cache_lock:
            self.memory_cache.clear()
        print("Memory thumbnail cache cleared.")

    # Optional: Method to clear disk cache (use with caution)
    # def clear_disk_cache(self):
    #     """Deletes all files in the thumbnail cache directory."""
    #     print(f"Clearing disk cache directory: {self.cache_dir}")
    #     with self.cache_lock: # Ensure consistency with memory cache clearing
    #         self.memory_cache.clear()
    #         for item in self.cache_dir.iterdir():
    #             if item.is_file():
    #                 try:
    #                     item.unlink()
    #                 except OSError as e:
    #                     print(f"Error deleting file {item}: {e}")
    #     print("Disk thumbnail cache cleared.")