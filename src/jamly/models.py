"""Supported Hybrid Transformer Demucs v4 separation presets."""
from __future__ import annotations

DEFAULT_MODEL = "htdemucs"
STEMS = ("drums", "bass", "other", "vocals")
MODELS = {
    DEFAULT_MODEL: {
        "label": "Balanced",
        "description": "Hybrid Transformer Demucs v4 default preset.",
    },
    "htdemucs_ft": {
        "label": "Higher quality",
        "description": "Fine-tuned 4-model Demucs v4 bag; cleaner transients, ~4x slower.",
    },
}


def validate_model(value: str) -> str:
    if not isinstance(value, str) or value not in MODELS:
        raise ValueError("Choose Balanced (htdemucs) or Higher quality (htdemucs_ft)")
    return value


def model_choices() -> list[dict[str, str]]:
    return [{"id": name, **details} for name, details in MODELS.items()]
