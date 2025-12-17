from pathlib import Path
from typing import List, Optional
from collections import defaultdict
from PIL import Image

from image_processing.predictor import WaifuTagger

from PyQt6.QtCore import pyqtSignal, QObject

from database.models import TagPrediction
import config
import traceback

class ImageTaggerModel(QObject):
    """
    Manages the WaifuTagger model loading, prediction, and result processing.
    Acts as a wrapper around the core predictor logic.
    """
    # Add a signal to notify the GUI about load errors
    model_load_error_signal = pyqtSignal(str)
    # Add signals for state changes
    model_loaded_signal = pyqtSignal()
    model_unloaded_signal = pyqtSignal()

    def __init__(self, model_path: Path = config.MODEL_PATH, csv_path: Path = config.TAGS_CSV_PATH, use_gpu: bool = True):
        """
        Initializes the ImageTaggerModel.

        Args:
            model_path: Path to the ONNX model file. Defaults to path from config.
            csv_path: Path to the tags CSV file. Defaults to path from config.
            use_gpu: Whether to attempt using the GPU. Defaults to True.
        """
        super().__init__() # Call QObject initializer
        self.model_path = model_path
        self.csv_path = csv_path
        self.use_gpu = use_gpu
        self.tagger: Optional[WaifuTagger] = None
        self._load_attempted = False # Track if load has been tried
        self._load_failed = False    # Track if the last load attempt failed
        print(f"ImageTaggerModel initialized. Model: {self.model_path}, CSV: {self.csv_path}, Use GPU: {self.use_gpu}")

    def load_model(self) -> bool:
        """
        Loads the underlying WaifuTagger model if not already loaded.
        Tracks load attempts and failures. Emits a signal on the first failure.

        Returns:
            True if the model is loaded successfully (or was already loaded), False otherwise.
        """
        # Prevent repeated failed attempts in the same session unless state is reset
        if self._load_attempted and self._load_failed:
            print("Skipping model load attempt: Previous attempt failed.")
            return False

        if self.tagger is None:
            first_attempt = not self._load_attempted # Is this the very first attempt?
            self._load_attempted = True # Mark that we are trying/have tried
            self._load_failed = False   # Assume success initially for this attempt
            print("Loading WaifuTagger model...")
            try:
                # Pass paths as strings, as WaifuTagger expects strings
                self.tagger = WaifuTagger(model_path=str(self.model_path), csv_path=str(self.csv_path), use_gpu=self.use_gpu)
                # Explicitly trigger the ONNX model load within WaifuTagger
                self.tagger.load_model()
                # Verify model loaded successfully within tagger
                if self.tagger.model is None:
                     raise RuntimeError("WaifuTagger initialization succeeded, but internal ONNX model is still None.")
                print("WaifuTagger model loaded successfully.")
                self.model_loaded_signal.emit() # Notify listeners
                return True # Success
            except Exception as e:
                error_message = f"Error loading WaifuTagger model: {e}"
                print(error_message)
                traceback.print_exc() # Print full traceback for debugging
                self.tagger = None
                self._load_failed = True
                # Emit the error signal only on the first failure to avoid spamming the user
                if first_attempt:
                    self.model_load_error_signal.emit(error_message)
                return False # Failure
        else:
             # Already loaded successfully
             return True # Success

    def unload_model(self):
        """Unloads the underlying WaifuTagger model to free resources."""
        if self.tagger is not None:
            print("Unloading WaifuTagger model...")
            try:
                self.tagger.unload_model()
            except Exception as e:
                 print(f"Error during WaifuTagger unload_model: {e}")
            finally:
                 self.tagger = None
                 # Reset load state flags when explicitly unloading
                 self._load_attempted = False
                 self._load_failed = False
                 self._load_failed = False
                 print("WaifuTagger model unloaded.")
                 self.model_unloaded_signal.emit() # Notify listeners

    def determine_rating(self, predictions: List[TagPrediction]) -> str:
        """
        Determines the overall rating ('general', 'sensitive', 'explicit')
        based on the confidence scores of rating-specific tags.

        Args:
            predictions: A list of TagPrediction objects from a prediction run.

        Returns:
            The determined rating string.
        """
        # Filter predictions to get only 'rating' category
        rating_tags = [p for p in predictions if p.category.lower() == "rating"]

        if not rating_tags:
            return "sensitive" # Default rating if no rating tags found

        # Group these predictions by tag name in lowercase
        tag_groups = defaultdict(list)
        for p in rating_tags:
            tag_groups[p.tag.lower()].append(p)

        # For each group, get the maximum confidence
        tag_max_confidence = {tag: max(group, key=lambda x: x.confidence).confidence for tag, group in tag_groups.items()}

        # --- Rating Logic ---
        general_conf_threshold = 0.04
        if tag_max_confidence.get("general", 0) >= general_conf_threshold:
            return "general"

        sensitive_conf = tag_max_confidence.get("sensitive", 0)
        questionable_conf = tag_max_confidence.get("questionable", 0)
        explicit_conf = tag_max_confidence.get("explicit", 0)

        if explicit_conf > (sensitive_conf + questionable_conf):
            return "explicit"
        else:
            return "sensitive"
        # --- End Rating Logic ---

    def predict(self, image: Image.Image, general_threshold=0.35, character_threshold=0.85) -> List[TagPrediction]:
        """
        Runs tag prediction on an image using the WaifuTagger.

        Ensures the model is loaded before prediction. Formats the results
        into a list of TagPrediction objects.

        Args:
            image: The PIL Image object to predict tags for.
            general_threshold: Confidence threshold for general tags.
            character_threshold: Confidence threshold for character tags.

        Returns:
            A list of TagPrediction objects, sorted by confidence descending.
            Returns an empty list if prediction fails or model cannot load.
        """
        # Try loading; load_model now returns status and handles _load_failed flag internally.
        # It also emits the error signal on the *first* failure.
        if not self.load_model():
             print("Prediction failed: Model is not loaded or failed to load.")
             return [] # Return empty list immediately if model isn't ready

        # Ensure self.tagger is checked again just in case, though load_model should handle it
        if self.tagger is None:
             print("Prediction failed: Tagger is None even after successful load_model call (unexpected).")
             # This case indicates a potential logic error in load_model or state management.
             # Triggering the error signal here might be appropriate if it wasn't emitted before.
             if not self._load_failed: # Avoid duplicate signals if load_model already failed/emitted
                 self.model_load_error_signal.emit("Internal Error: Tagger became None unexpectedly.")
                 self._load_failed = True # Mark as failed state
             return []

        try:
            # Call the underlying tagger's predict method
            # WaifuTagger now returns Dict[str, float] for each category
            general_tags_dict, rating_dict, character_dict = self.tagger.predict(
                image,
                general_thresh=general_threshold,
                character_thresh=character_threshold
                # Pass other options like mcut if needed:
                # general_mcut_enabled=False,
                # character_mcut_enabled=False,
            )
        except Exception as e:
            print(f"Error during WaifuTagger prediction: {e}")
            # Optionally log traceback for unexpected errors during prediction itself
            traceback.print_exc()
            # Decide if a prediction error should mark the model as failed for future attempts.
            # Probably not, as it might be specific to the image.
            return [] # Return empty list on prediction error

        # Ensure all dictionaries are non-None (WaifuTagger might return None for characters)
        general_tags_dict = general_tags_dict or {}
        rating_dict = rating_dict or {}
        character_dict = character_dict or {}

        predictions: List[TagPrediction] = []

        # Add rating tags (category 'rating')
        for tag, confidence in rating_dict.items():
            predictions.append(TagPrediction(tag=tag, confidence=float(confidence), category="rating"))

        # Add general tags (category 'general') - Threshold applied within WaifuTagger.predict now
        for tag, confidence in general_tags_dict.items():
            # No need to check threshold again here if applied in predictor
            predictions.append(TagPrediction(tag=tag, confidence=float(confidence), category="general"))

        # Add character tags (category 'character') - Threshold applied within WaifuTagger.predict now
        # character_tags_added = False # Variable not used, removed
        for tag, confidence in character_dict.items():
            # No need to check threshold again here if applied in predictor
            predictions.append(TagPrediction(tag=tag, confidence=float(confidence), category="character"))
            # character_tags_added = True # Variable not used, removed

        # Add 'unidentified' character tag if no character tags met the threshold in the predictor
        # The predictor returns None or empty dict if no characters meet threshold.
        if not character_dict: # Check if the dictionary is empty (or None, handled by `or {}` above)
             # Add 'unidentified' if no characters were found above threshold
             predictions.append(TagPrediction(tag="unidentified", confidence=0.0, category="character"))

        # Sort final list by confidence
        return sorted(predictions, key=lambda x: x.confidence, reverse=True)