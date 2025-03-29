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

# Define supported image file extensions (lowercase, include leading dot)
SUPPORTED_FORMATS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff', '.tif')

# Ensure data directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# --- Other potential configurations ---
# Example: Default thresholds (can be moved here later if needed)

# Example: Thumbnail settings