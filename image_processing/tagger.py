from pathlib import Path
from typing import List, Optional
from collections import defaultdict
from PIL import Image

# Absolute imports
from image_processing.predictor import WaifuTagger
from database.models import TagPrediction
# Import config to potentially get default paths or settings if needed later
import config

class ImageTaggerModel:
    """
    Manages the WaifuTagger model loading, prediction, and result processing.
    Acts as a wrapper around the core predictor logic.
    """
    def __init__(self, model_path: Path = config.MODEL_PATH, csv_path: Path = config.TAGS_CSV_PATH, use_gpu: bool = True):
        """
        Initializes the ImageTaggerModel.

        Args:
            model_path: Path to the ONNX model file. Defaults to path from config.
            csv_path: Path to the tags CSV file. Defaults to path from config.
            use_gpu: Whether to attempt using the GPU. Defaults to True.
        """
        self.model_path = model_path
        self.csv_path = csv_path
        self.use_gpu = use_gpu
        self.tagger: Optional[WaifuTagger] = None
        print(f"ImageTaggerModel initialized. Model: {self.model_path}, CSV: {self.csv_path}, Use GPU: {self.use_gpu}")

    def load_model(self):
        """Loads the underlying WaifuTagger model if not already loaded."""
        if self.tagger is None:
            print("Loading WaifuTagger model...")
            try:
                # Pass paths as strings, as WaifuTagger expects strings
                self.tagger = WaifuTagger(model_path=str(self.model_path), csv_path=str(self.csv_path), use_gpu=self.use_gpu)
                # WaifuTagger's __init__ or load_model handles the actual loading now
                # self.tagger.load_model() # load_model is called within WaifuTagger's predict if needed
                print("WaifuTagger instance created.")
            except Exception as e:
                print(f"Error creating WaifuTagger instance: {e}")
                self.tagger = None # Ensure tagger is None if creation failed
                # Optionally re-raise or handle the error appropriately
                raise

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
                 # Optional: Force garbage collection if memory issues persist
                 print("WaifuTagger model unloaded.")


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
        # Priority 1: General Check
        # If 'general' confidence is reasonably high, classify as general.
        # Adjusted threshold based on typical model outputs might be needed.
        general_conf_threshold = 0.04 # Keep original threshold for now
        if tag_max_confidence.get("general", 0) >= general_conf_threshold:
            return "general"

        # Priority 2: Explicit vs. Sensitive/Questionable
        # Compare 'explicit' confidence against the sum of 'sensitive' and 'questionable'.
        sensitive_conf = tag_max_confidence.get("sensitive", 0)
        questionable_conf = tag_max_confidence.get("questionable", 0)
        explicit_conf = tag_max_confidence.get("explicit", 0)

        # If explicit confidence is higher, classify as explicit.
        if explicit_conf > (sensitive_conf + questionable_conf):
            return "explicit"
        else:
            # Otherwise, classify as sensitive (covers sensitive, questionable, or cases where general was low).
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
            Returns an empty list if prediction fails.
        """
        if self.tagger is None:
            try:
                self.load_model()
                if self.tagger is None: # Check again if loading failed
                     print("Prediction failed: Model could not be loaded.")
                     return []
            except Exception as e:
                 print(f"Prediction failed: Error loading model: {e}")
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
        character_tags_added = False
        for tag, confidence in character_dict.items():
            # No need to check threshold again here if applied in predictor
            predictions.append(TagPrediction(tag=tag, confidence=float(confidence), category="character"))
            character_tags_added = True

        # Add 'unidentified' character tag if no character tags met the threshold in the predictor
        # The predictor returns None if no characters meet threshold, so check that.
        # Or, if it returns an empty dict, check that. Let's assume empty dict for now.
        if not character_dict: # Check if the dictionary is empty
             # Check if 'unidentified' should always be added or only if NO characters were found
             # Let's assume add if no characters were found above threshold
             predictions.append(TagPrediction(tag="unidentified", confidence=0.0, category="character"))


        # Sort final list by confidence
        return sorted(predictions, key=lambda x: x.confidence, reverse=True)