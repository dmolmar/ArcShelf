import sys
from pathlib import Path

# Si se ejecuta como script, el directorio base es el que contiene este archivo config.py
BASE_DIR = Path(__file__).resolve().parent

# Define key directories relative to the base directory
DATA_DIR = BASE_DIR / 'data'
MODELS_DIR = BASE_DIR / 'models'
CACHE_DIR = DATA_DIR / 'thumbnail_cache'

# Define specific file paths
DB_PATH = DATA_DIR / 'images.db'
MODEL_PATH = MODELS_DIR / 'model.onnx'
TAGS_CSV_PATH = MODELS_DIR / 'selected_tags.csv'

ICON_PATH = BASE_DIR / 'arcueid.ico'

# Ensure data directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# --- Other potential configurations ---
# Example: Default thresholds (can be moved here later if needed)
# DEFAULT_GENERAL_THRESHOLD = 0.35
# DEFAULT_CHARACTER_THRESHOLD = 0.85

# Example: Thumbnail settings
# THUMBNAIL_SIZE = (256, 256) # Or calculate dynamically based on UI

print(f"Base Directory: {BASE_DIR}")
print(f"Data Directory: {DATA_DIR}")
print(f"Models Directory: {MODELS_DIR}")
print(f"Database Path: {DB_PATH}")
print(f"Cache Directory: {CACHE_DIR}")