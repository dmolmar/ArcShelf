# ArcShelf Project Structure Documentation

This document provides an overview of the ArcShelf desktop application, its main features, file structure, and the role of key files within the project.

## Application Overview

ArcShelf is a desktop application built with PyQt6 for managing, viewing, searching, and automatically tagging image collections on Windows. Its core functionalities include:

*   **Image Management:** Adding and removing directories containing image collections.
*   **Image Display:** A customizable gallery view with adjustable row height and an advanced preview area with zoom and panning.
*   **Image Tagging:** Automatic tagging of images using an AI model (`wd-eva02-large-tagger-v3`) to identify ratings, characters, and general tags.
*   **Search Functionality:** Powerful search capabilities using logical operators (AND, OR, NOT), tag suggestions, and similarity search.
*   **Database:** Stores image metadata, tags, and relationships for efficient searching and retrieval.
*   **Thumbnail Caching:** Generates and caches image thumbnails for faster loading in the gallery.
*   **Background Processing:** Utilizes worker threads for long-running tasks like image analysis and directory processing to keep the UI responsive.
*   **Requirements Checking and Installation:** Provides a dialog to check for necessary dependencies and download/install them.
*   **Image Export:** Allows exporting images to JPG format with customizable quality and resolution.
*   **Duplicate Detection:** Identifies potential duplicate images based on tag similarity.

## File Structure

The project is organized into several directories, each responsible for a specific part of the application:

```
.
├── config.py               # Application configuration (paths, settings)
├── main.py                 # Application entry point
├── README.md               # English README (project description, features, installation, usage)
├── README_es.md            # Spanish README
├── requirements.txt        # Project dependencies list
├── run.bat                 # Windows batch script for setup and launching
├── arcueid.ico             # Application icon
├── readme_preview.png      # Image used in READMEs
├── database/               # Database management and models
│   ├── db_manager.py       # Handles SQLite database interactions
│   └── models.py           # Defines data models (e.g., TagPrediction)
├── gui/                    # Graphical User Interface components
│   ├── main_window.py      # Main application window and core UI logic
│   ├── dialogs/            # Various dialog windows
│   │   ├── export_jpg.py   # Dialog for exporting images as JPG
│   │   ├── manage_directories.py # Dialog for managing directories and duplicates
│   │   └── requirements_dialog.py # Dialog for checking and installing requirements
│   ├── models/             # (Potentially contains GUI-specific models, currently empty)
│   └── widgets/            # Custom reusable UI widgets
│       ├── advanced_search.py # Widget for the advanced search input and suggestions
│       ├── directory_list_item.py # Custom widget for directory list items
│       ├── drag_drop_area.py # Widget for image preview and drag-drop
│       └── image_label.py    # Custom QLabel for displaying image thumbnails in the gallery
├── image_processing/       # Image analysis and processing
│   ├── predictor.py        # Handles loading and running the AI tagging model (WaifuTagger)
│   ├── tagger.py           # Wrapper around the predictor, manages model state and prediction results
│   └── thumbnail.py        # Manages thumbnail generation and caching
├── models/                 # AI model files (e.g., .onnx model, tags CSV)
├── search/                 # Search query parsing and evaluation
│   ├── query_evaluator.py  # Evaluates search query AST against the database
│   └── query_parser.py     # Parses search query strings into an AST
└── utils/                  # Utility functions and classes
    ├── path_utils.py       # Path normalization utilities
    └── workers.py          # Generic and specialized worker classes for background tasks
```

*(Note: Excluded directories like `__pycache__` and `.git` contents as they are build artifacts or version control related.)*

## Key File Descriptions

Here's a summary of the role of the significant files:

*   **`config.py`**: Defines application-wide configuration settings, including paths to data, models, database, and cache directories, as well as supported image formats. It's crucial for setting up the application environment.
*   **`main.py`**: The application's entry point. Initializes the PyQt application, enforces launching via `run.bat`, sets up the main window, and starts the application event loop.
*   **`README.md` / `README_es.md`**: Provide user-facing documentation, including features, requirements, installation, and basic usage instructions in English and Spanish.
*   **`requirements.txt`**: Lists the Python packages and their versions required for the project. Used by pip for dependency management.
*   **`run.bat`**: A Windows batch script that automates the setup process (checking Python, creating a virtual environment, installing dependencies, downloading model files) and launches the application. It's the recommended way to start ArcShelf.
*   **`database/db_manager.py`**: Manages all interactions with the SQLite database. It handles schema creation, adding/updating/deleting image and tag data, cleaning up orphaned records, and retrieving data for various parts of the application. Uses threading locks for safe concurrent access.
*   **`database/models.py`**: Defines simple data structures (dataclasses) used to represent data, such as `TagPrediction`.
*   **`gui/main_window.py`**: Defines the main `QMainWindow` for the application. It orchestrates the UI layout, connects signals and slots between different widgets and backend components, manages the image gallery display (loading thumbnails, pagination, sorting), handles user interactions like image clicks and search requests, and manages background worker tasks.
*   **`gui/dialogs/export_jpg.py`**: Defines the `ExportAsJPGDialog`, a dialog window that allows users to export images to JPG format with options for quality and resolution.
*   **`gui/dialogs/manage_directories.py`**: Defines the `ManageDirectoriesDialog`, a dialog window for managing included directories, processing images within them, detecting duplicates, and reprocessing images.
*   **`gui/dialogs/requirements_dialog.py`**: Defines the `RequirementsDialog`, which checks for and facilitates the installation of necessary system and Python requirements, including downloading model files.
*   **`gui/widgets/advanced_search.py`**: Implements the custom widget for the search input field and tag suggestions. It handles user input and interacts with the main window for search execution and suggestion management.
*   **`gui/widgets/directory_list_item.py`**: A custom `QWidget` used within the `ManageDirectoriesDialog` to display a single directory with a checkbox for its active state.
*   **`gui/widgets/drag_drop_area.py`**: Implements a custom `QGraphicsView` widget used for the image preview panel. It handles drag-and-drop events for loading images, manages levels of detail (LODs) for efficient zooming and panning, and provides a context menu for image-related actions.
*   **`gui/widgets/image_label.py`**: A custom `QLabel` used to display individual image thumbnails in the gallery. It handles mouse clicks to display the image in the preview and provides a context menu with actions like opening, copying, and triggering similarity search.
*   **`image_processing/predictor.py`**: Contains the `WaifuTagger` class, which is the core component for running the AI tagging model. It handles loading the ONNX model and tag labels, preprocessing images for the model, running inference, and processing the raw output into tag probabilities with optional thresholding.
*   **`image_processing/tagger.py`**: Acts as a higher-level manager for the `WaifuTagger`. It handles the loading and unloading of the model, determines the overall image rating based on prediction results, and formats the predictions into a list of `TagPrediction` objects. It also emits signals for model loading errors.
*   **`image_processing/thumbnail.py`**: Manages the generation, storage, and retrieval of image thumbnails. It uses both an in-memory cache and a disk cache (storing thumbnails as WEBP files) for performance.
*   **`search/query_evaluator.py`**: Takes the parsed search query (as an AST) and translates it into database queries to find matching image IDs. It handles the logic for boolean operators (AND, OR, NOT) and scopes the search to the currently active directories.
*   **`search/query_parser.py`**: Responsible for parsing the user's search query string. It tokenizes the input and builds an Abstract Syntax Tree (AST) that represents the logical structure of the query.
*   **`utils/path_utils.py`**: Provides utility functions for consistent handling and normalization of file paths across different operating systems.
*   **`utils/workers.py`**: Contains generic and specialized `QRunnable` classes (`Worker`, `ThumbnailLoader`) for executing tasks in separate threads using a `QThreadPool`, preventing the main GUI thread from freezing during long operations.