"""Per-model capability profiles (taken from Smart Control Plus device configs)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .protocol import TouchAction as A
from .protocol import TouchPattern as P


@dataclass(frozen=True)
class Profile:
    name: str
    eq_band_labels: tuple[str, ...] = ()
    eq_presets: dict[str, tuple[float, ...]] = field(default_factory=dict)
    eq_bass_boost: bool = False
    transparency_steps: tuple[int, ...] = (0, 25, 50, 75, 100)
    anti_wind: bool = False
    sidetone_steps: int = 0
    auto_power_off_minutes: tuple[int, ...] = ()
    touch_patterns: tuple[P, ...] = ()
    touch_actions: dict[P, tuple[A, ...]] = field(default_factory=dict)
    settings: tuple[str, ...] = ()       # names of boolean/enum settings supported


_MTW4_COMMON = (A.NONE, A.PLAY_PAUSE, A.NEXT_SONG, A.PREVIOUS_SONG, A.TRANSPARENT_HEARING,
                A.VOICE_ASSISTANT, A.ANC_ON_OFF)

# products/0009 ("mtw4-0.json"), firmware >= 5.19.1
MTW4 = Profile(
    name="MOMENTUM True Wireless 4",
    eq_band_labels=("63 Hz", "250 Hz", "1 kHz", "4 kHz", "8 kHz"),
    eq_presets={
        "Flat": (0.0, 0.0, 0.0, 0.0, 0.0),
        "Rock": (-2.5, 0.5, 2.5, -0.5, -3.5),
        "Pop": (0.0, -2.5, 0.0, 2.5, -1.0),
        "Dance": (3.0, -1.0, -2.0, 0.0, 1.0),
        "Hip-hop": (2.5, 1.5, -1.5, -1.5, -1.5),
        "Classical": (-3.5, -1.5, 0.0, 1.5, 0.5),
        "Movie": (-3.0, -1.0, 0.0, 1.0, -4.0),
        "Jazz": (-4.0, 0.0, 1.0, 3.0, -1.0),
    },
    eq_bass_boost=True,
    anti_wind=True,
    sidetone_steps=4,
    auto_power_off_minutes=(15, 30, 60, 120),
    touch_patterns=(P.TAP, P.DOUBLE_TAP, P.TRIPLE_TAP, P.LONG_PRESS),
    # "customizable_patterns": identical for both buttons
    touch_actions={
        P.TAP: _MTW4_COMMON,
        P.DOUBLE_TAP: _MTW4_COMMON,
        P.TRIPLE_TAP: _MTW4_COMMON + (A.TOGGLE_TOUCH_LOCK,),
        P.LONG_PRESS: (A.NONE, A.TRANSPARENT_HEARING, A.VOICE_ASSISTANT, A.ANC_ON_OFF,
                       A.TOGGLE_TOUCH_LOCK, A.VOLUME_UP, A.VOLUME_DOWN),
    },
    settings=("smart_pause", "on_head_detection", "auto_answer", "comfort_call", "gaming_mode",
              "aptx_96khz", "aptx_lossless", "prompts", "sidetone", "podcast_mode", "auto_power_off"),
)

GENERIC = Profile(name="Sennheiser headphones")

_BY_MODEL_PREFIX = {
    "MOMENTUM TW 4": MTW4,
    "MTW4": MTW4,
}


def profile_for(variant: str | None, model_id: str | None = None) -> Profile:
    for s in (variant or "", model_id or ""):
        for prefix, prof in _BY_MODEL_PREFIX.items():
            if s.upper().startswith(prefix):
                return prof
    return GENERIC
