import numpy as np
import onnxruntime as rt
import pandas as pd
from PIL import Image
from typing import Tuple, List, Dict, Optional

# Attempt relative import for config when run as part of the package
try:
    from .. import config
except ImportError:
    # Fallback for when run as a standalone script (e.g., during testing)
    # This assumes the script is run from the ARC-EXPLORER directory
    # or that arc_explorer is in the Python path.
    # A more robust solution might involve setting PYTHONPATH or using a different structure.
    import config

class WaifuTagger:
    def __init__(self, model_path: str, csv_path: str, use_gpu: bool = True):
        """
        Initialize the WaifuDiffusion tagger with local model and CSV files.

        Args:
            model_path: Path to the .onnx model file
            csv_path: Path to the selected_tags.csv file
            use_gpu: Whether to use GPU for inference. Defaults to True.
        """
        self.model_path = model_path
        self.csv_path = csv_path
        self.model_target_size = None
        self.use_gpu = use_gpu  # Store the GPU usage preference
        self.model = None  # Initialize model to None
        self.providers = None  # Initialize providers to None
        self.tag_names = None
        self.rating_indexes = None
        self.general_indexes = None
        self.character_indexes = None
        self.load_labels_and_tags()

    def load_labels_and_tags(self):
        """Load tags and labels from CSV"""
        tags_df = pd.read_csv(self.csv_path)
        sep_tags = self.load_labels(tags_df)

        self.tag_names = sep_tags[0]
        self.rating_indexes = sep_tags[1]
        self.general_indexes = sep_tags[2]
        self.character_indexes = sep_tags[3]

    def load_labels(self, dataframe: pd.DataFrame) -> Tuple[List[str], List[int], List[int], List[int]]:
        """Load and process labels from the CSV file."""
        # List of special kaomoji tags that should not have underscores replaced
        kaomojis = [
            "0_0", "(o)_(o)", "+_+", "+_-", "._.", "<o>_<o>", "<|>_<|>",
            "=_=", ">_<", "3_3", "6_9", ">_o", "@_@", "^_^", "o_o",
            "u_u", "x_x", "|_|", "||_||"
        ]

        name_series = dataframe["name"]
        name_series = name_series.map(
            lambda x: x.replace("_", " ") if x not in kaomojis else x
        )
        tag_names = name_series.tolist()

        # Get indices for different tag categories
        rating_indexes = list(np.where(dataframe["category"] == 9)[0])
        general_indexes = list(np.where(dataframe["category"] == 0)[0])
        character_indexes = list(np.where(dataframe["category"] == 4)[0])

        return tag_names, rating_indexes, general_indexes, character_indexes

    def define_providers(self):
        """Define providers based on GPU usage preference."""
        if self.use_gpu:
            # Check if CUDAExecutionProvider is available
            available_providers = rt.get_available_providers()
            if 'CUDAExecutionProvider' in available_providers:
                self.providers = [
                    ('CUDAExecutionProvider', {
                        'device_id': 0,
                        'arena_extend_strategy': 'kNextPowerOfTwo',
                        'gpu_mem_limit': 2 * 1024 * 1024 * 1024, # Limit to 2GB VRAM initially
                        'cudnn_conv_algo_search': 'DEFAULT', # Use DEFAULT for potentially faster startup
                        'do_copy_in_default_stream': True,
                    }),
                    'CPUExecutionProvider'
                ]
                print("Using CUDAExecutionProvider.")
            else:
                print("CUDAExecutionProvider not available. Falling back to CPU.")
                self.providers = ['CPUExecutionProvider']
        else:
            print("GPU usage disabled. Using CPUExecutionProvider.")
            self.providers = ['CPUExecutionProvider']


    def load_model(self):
        """Load the ONNX model."""
        if self.model is None:  # Only load if model is not already loaded
            self.define_providers()
            try:
                print(f"Attempting to load model: {self.model_path}")
                print(f"Using providers: {self.providers}")
                # Provide session options if needed, e.g., for logging
                sess_options = rt.SessionOptions()
                # sess_options.log_severity_level = 0 # Uncomment for verbose logging
                self.model = rt.InferenceSession(str(self.model_path), sess_options=sess_options, providers=self.providers)
                print("Model loaded successfully.")
            except Exception as e:
                print(f"Error during model loading with providers {self.providers}. Error: {e}")
                # Attempt fallback to CPU if not already the only provider
                if self.providers != ['CPUExecutionProvider']:
                    print("Falling back to CPUExecutionProvider.")
                    try:
                        self.providers = ['CPUExecutionProvider']
                        sess_options = rt.SessionOptions()
                        self.model = rt.InferenceSession(str(self.model_path), sess_options=sess_options, providers=self.providers)
                        print("Model loaded successfully with CPU.")
                    except Exception as fallback_e:
                        print(f"Failed to load model even with CPU fallback. Error: {fallback_e}")
                        self.model = None # Ensure model is None if loading failed
                        raise fallback_e # Re-raise the exception after logging
                else:
                     self.model = None # Ensure model is None if loading failed
                     raise e # Re-raise the original exception if CPU was already the only provider

            if self.model:
                _, height, width, _ = self.model.get_inputs()[0].shape
                self.model_target_size = height
                print(f"Model input size: {self.model_target_size}x{self.model_target_size}")


    def unload_model(self):
        """Unload the ONNX model and free memory."""
        if self.model is not None:
            del self.model
            self.model = None
            self.model_target_size = None
            # Keep providers definition? Maybe not necessary to clear here.
            # self.providers = None
            print("Model unloaded.")
            # Consider adding gc.collect() if memory issues persist

    def prepare_image(self, image: Image.Image) -> np.ndarray:
        """Prepare image for model inference."""

        if self.model_target_size is None:
             # Try loading the model if it wasn't loaded before calling predict
            print("Model not loaded. Attempting to load model before preparing image.")
            self.load_model()
            if self.model_target_size is None:
                 raise RuntimeError("Model could not be loaded. Cannot prepare image.")


        target_size = self.model_target_size

        # Robustly convert to RGB, handling various modes
        if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
            # Convert to RGBA first if needed, composite with white background
            try:
                image = image.convert('RGBA')
                canvas = Image.new("RGBA", image.size, (255, 255, 255))
                canvas.alpha_composite(image)
                image = canvas.convert("RGB")
            except Exception as e:
                 print(f"Error converting image with transparency to RGB: {e}")
                 # Fallback: Convert directly to RGB, ignoring transparency
                 image = image.convert("RGB")

        elif image.mode != 'RGB':
            # Convert other modes directly to RGB
            image = image.convert("RGB")

        # Pad image to square
        image_shape = image.size
        max_dim = max(image_shape)
        pad_left = (max_dim - image_shape[0]) // 2
        pad_top = (max_dim - image_shape[1]) // 2

        padded_image = Image.new("RGB", (max_dim, max_dim), (255, 255, 255))
        padded_image.paste(image, (pad_left, pad_top))

        # Resize
        if max_dim != target_size:
            padded_image = padded_image.resize(
                (target_size, target_size),
                Image.Resampling.BICUBIC,  # Use Image.Resampling for clarity
            )

        # Convert to numpy array
        image_array = np.asarray(padded_image, dtype=np.float32)

        # Convert PIL-native RGB to BGR
        image_array = image_array[:, :, ::-1]

        return np.expand_dims(image_array, axis=0)

    def mcut_threshold(self, probs: np.ndarray) -> float:
        """
        Calculate Maximum Cut Thresholding (MCut).

        Args:
            probs: Array of probability values

        Returns:
            Calculated threshold value
        """
        # Ensure probs is not empty
        if probs.size == 0:
            return 0.0 # Or some default threshold

        sorted_probs = probs[probs.argsort()[::-1]]
        difs = sorted_probs[:-1] - sorted_probs[1:]

        # Handle case where there are no differences (e.g., all probs are the same)
        if difs.size == 0:
             return sorted_probs[0] / 2 # Arbitrary threshold, maybe needs refinement

        t = difs.argmax()
        thresh = (sorted_probs[t] + sorted_probs[t + 1]) / 2
        return thresh

    def predict(
        self,
        image: Image.Image,
        general_thresh: float = 0.35,
        general_mcut_enabled: bool = False,
        rating_thresh: float = 0.5, # Note: This threshold is not currently used in the logic below
        character_thresh: float = 0.85,
        character_mcut_enabled: bool = False,
    ) -> Tuple[Dict[str, float], Dict[str, float], Optional[Dict[str, float]]]:
        """
        Predict tags for the given image.

        Args:
            image: PIL Image object
            general_thresh: Threshold for general tags
            general_mcut_enabled: Whether to use MCut for general tags
            rating_thresh: Threshold for rating tags (currently unused)
            character_thresh: Threshold for character tags
            character_mcut_enabled: Whether to use MCut for character tags

        Returns:
            Tuple containing:
            - Dictionary of general tags with probabilities
            - Dictionary of rating tags with probabilities
            - Dictionary of character tags with probabilities (or None if no characters meet the threshold)
        """
        if self.model is None:
            print("Model not loaded. Loading model before prediction.")
            self.load_model()
            if self.model is None:
                raise RuntimeError("Model could not be loaded. Cannot perform prediction.")


        prepared_image = self.prepare_image(image)

        # Run inference
        input_name = self.model.get_inputs()[0].name
        label_name = self.model.get_outputs()[0].name
        preds = self.model.run([label_name], {input_name: prepared_image})[0]

        # Process predictions
        # Ensure tag_names is loaded
        if self.tag_names is None:
             self.load_labels_and_tags() # Should have been called in __init__

        labels = list(zip(self.tag_names, preds[0].astype(float)))

        # Get ratings
        ratings_names = [labels[i] for i in self.rating_indexes]
        # Apply rating_thresh if needed in the future, currently all ratings are returned
        ratings = {name: prob for name, prob in ratings_names}

        # Process general tags
        general_names = [labels[i] for i in self.general_indexes]

        current_general_thresh = general_thresh
        if general_mcut_enabled:
            general_probs = np.array([x[1] for x in general_names])
            current_general_thresh = self.mcut_threshold(general_probs)
            print(f"Using MCut general threshold: {current_general_thresh:.4f}")


        general_res = {name: prob for name, prob in general_names if prob > current_general_thresh}

        # Process character tags
        character_names = [labels[i] for i in self.character_indexes]

        current_character_thresh = character_thresh
        if character_mcut_enabled:
            character_probs = np.array([x[1] for x in character_names])
            mcut_char_thresh = self.mcut_threshold(character_probs)
            # Ensure MCut doesn't go too low for characters
            current_character_thresh = max(0.15, mcut_char_thresh)
            print(f"Using MCut character threshold: {current_character_thresh:.4f}")


        character_res = {name: prob for name, prob in character_names if prob > current_character_thresh}
        if not character_res:
            character_res = None # Return None if no characters meet threshold

        return general_res, ratings, character_res
