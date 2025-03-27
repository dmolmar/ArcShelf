from dataclasses import dataclass

@dataclass
class TagPrediction:
    """Represents a single tag prediction with its confidence and category."""
    tag: str
    confidence: float
    category: str