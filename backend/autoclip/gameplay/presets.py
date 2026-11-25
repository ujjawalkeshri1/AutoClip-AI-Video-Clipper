"""Low, Medium and High processing presets.

Keys are kept stable (``low_resource``, ``medium``, ``quality``) because they are stored in
settings and sent by the API; the UI shows them as Low, Medium and High. ``low`` and ``high``
are accepted as aliases.
"""

from __future__ import annotations

from ..config import Settings

#: What each preset sets. Anything not listed is left as the user configured it.
PRESETS: dict[str, dict] = {
    # Fastest; safe for 8 GB machines. Whisper base, ~1 frame/s, speech only around candidates.
    "low_resource": {
        "whisper_model": "base", "sample_fps": 1.0, "transcribe_scope": "candidates", "max_candidates": 12,
    },
    # Balanced for a 16 GB laptop: better speech accuracy and finer motion sampling, still only
    # transcribing around candidates.
    "medium": {
        "whisper_model": "small", "sample_fps": 2.0, "transcribe_scope": "candidates", "max_candidates": 15,
    },
    # Slowest, most thorough: whole-video transcript at 4 frames/s.
    "quality": {
        "whisper_model": "small", "sample_fps": 4.0, "transcribe_scope": "full", "max_candidates": 20,
    },
}


ALIASES = {"low": "low_resource", "high": "quality"}
LABELS = {"low_resource": "Low", "medium": "Medium", "quality": "High"}


def apply_preset(settings: Settings, name: str) -> Settings:
    name = ALIASES.get(name, name)
    if name not in PRESETS:
        raise ValueError(f"Unknown preset {name!r}. Available: {', '.join(PRESETS)}")
    p = PRESETS[name]
    settings.gameplay.preset = name  # type: ignore[assignment]
    settings.whisper.model = p["whisper_model"]
    settings.gameplay.sample_fps = p["sample_fps"]
    settings.gameplay.transcribe_scope = p["transcribe_scope"]  # type: ignore[assignment]
    settings.gameplay.max_candidates = p["max_candidates"]
    return settings
