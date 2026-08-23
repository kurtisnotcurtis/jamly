"""The deliberately small set of supported separation presets."""
from __future__ import annotations

DEFAULT_MODEL = "htdemucs"
DEFAULT_STEM = "drums"
STEMS_4 = ("drums", "bass", "other", "vocals")
STEMS_6 = ("drums", "bass", "other", "vocals", "guitar", "piano")
ALL_OUTPUT_KINDS = frozenset(STEMS_6 + tuple("no_" + s for s in STEMS_6) + ("metadata",))
MODELS = {
    DEFAULT_MODEL: {
        "label": "Balanced",
        "description": "Good quality with faster processing. Recommended for most tracks.",
    },
    "htdemucs_ft": {
        "label": "Higher quality",
        "description": "Fine-tuned Demucs; may improve separation, but takes about 4x longer "
                       "and downloads more model weights on first use. Improvements vary by track.",
    },
    "htdemucs_6s": {
        "label": "6-stem (HTDemucs)",
        "description": "Six-stem Hybrid Transformer Demucs (drums, bass, guitar, piano, vocals, other); "
                       "single-pass model required for isolated guitar or piano splits.",
    },
}
MODEL_STEMS = {
    DEFAULT_MODEL: STEMS_4,
    "htdemucs_ft": STEMS_4,
    "htdemucs_6s": STEMS_6,
}
STEM_MODES = {
    "drums": {
        "label": "Drums (drums + drumless)",
        "description": "Isolated drums and a drumless backing track.",
        "models": ("htdemucs", "htdemucs_ft", "htdemucs_6s"),
    },
    "vocals": {
        "label": "Vocals / Karaoke (vocals + instrumental)",
        "description": "Isolated vocals and a vocal-free karaoke instrumental backing track.",
        "models": ("htdemucs", "htdemucs_ft", "htdemucs_6s"),
    },
    "bass": {
        "label": "Bass (bass + bassless)",
        "description": "Isolated bass and a bassless backing track.",
        "models": ("htdemucs", "htdemucs_ft", "htdemucs_6s"),
    },
    "guitar": {
        "label": "Guitar (guitar + guitarless)",
        "description": "Isolated guitar and a guitarless backing track (requires 6-stem preset).",
        "models": ("htdemucs_6s",),
    },
    "piano": {
        "label": "Piano (piano + pianoless)",
        "description": "Isolated piano and a pianoless backing track (requires 6-stem preset).",
        "models": ("htdemucs_6s",),
    },
    "other": {
        "label": "Other (other + no-other)",
        "description": "Remaining accompaniment stem and its complement.",
        "models": ("htdemucs", "htdemucs_ft", "htdemucs_6s"),
    },
    "all": {
        "label": "All stems (individual tracks)",
        "description": "Export every stem produced by the preset (4 or 6 stems) with one shared peak-safe gain.",
        "models": ("htdemucs", "htdemucs_ft", "htdemucs_6s"),
    },
}


def validate_model(value: str) -> str:
    if not isinstance(value, str) or value not in MODELS:
        raise ValueError(
            "Choose Balanced (htdemucs), Higher quality (htdemucs_ft), or 6-stem (htdemucs_6s)"
        )
    return value


def model_stems(value: str = DEFAULT_MODEL) -> tuple[str, ...]:
    validate_model(value)
    return MODEL_STEMS[value]


def validate_stem(value: str, model: str | None = None) -> str:
    if not isinstance(value, str) or value not in STEM_MODES:
        raise ValueError(
            "Choose a valid stem target: drums, vocals, bass, guitar, piano, other, or all"
        )
    if model is not None:
        validate_model(model)
        if value != "all" and value not in MODEL_STEMS[model]:
            raise ValueError(
                "Stem %r requires the 6-stem (htdemucs_6s) preset" % value
            )
    return value


def expected_output_kinds(stem: str = DEFAULT_STEM, model: str = DEFAULT_MODEL) -> tuple[str, ...]:
    validate_stem(stem, model)
    if stem == "all":
        return model_stems(model)
    return (stem, "no_" + stem)


def model_choices() -> list[dict[str, str]]:
    return [{"id": name, **details} for name, details in MODELS.items()]


def stem_choices() -> list[dict]:
    return [
        {"id": name, "label": details["label"], "description": details["description"],
         "models": list(details["models"])}
        for name, details in STEM_MODES.items()
    ]
