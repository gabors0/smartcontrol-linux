"""Sennheiser GAIA vendor (0x0495) features, PDUs and value tables.

Recovered from Smart Control Plus 1.6.3 (Dart AOT, via the dispatch-table
getters of each command class) and verified on a MOMENTUM True Wireless 4
where marked. See docs/PROTOCOL.md for details and evidence levels.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Feature(IntEnum):
    CORE = 0x00
    UPGRADE_EXTENSION = 0x01
    DEVICE = 0x02
    POWER = 0x03
    GENERIC_AUDIO = 0x04
    EARBUD = 0x06
    USER_EQ = 0x08
    VERSIONS = 0x09
    DEVICE_MANAGEMENT = 0x0A
    MMI_CONFIG = 0x0B
    TRANSPARENT_HEARING = 0x0C
    ANC = 0x0D
    TWS = 0x0E
    FITTING_TEST = 0x0F
    PERSONALIZED_SOUND = 0x10
    LOCAL_NAME = 0x14
    AURACAST = 0x15
    FIND_DEVICE = 0x16


# Core (both vendors)
CORE_GET_API_VERSION = 0x00
CORE_GET_SUPPORTED_FEATURES = 0x01
CORE_GET_SERIAL = 0x03           # Qualcomm vendor
CORE_GET_VARIANT = 0x04          # Qualcomm vendor
CORE_GET_APP_VERSION = 0x05      # Qualcomm vendor
CORE_REGISTER_NOTIFICATION = 0x07
CORE_UNREGISTER_NOTIFICATION = 0x08
# Deliberately not exposed: Sennheiser core 0x40 (factory reset), power 0x0F (reboot).


@dataclass(frozen=True)
class Prop:
    """A get/set property. Notifications for it arrive with the GET pdu."""
    feature: Feature
    get: int
    set: int | None = None


# ---- device (0x02)
ON_HEAD_DETECTION = Prop(Feature.DEVICE, 0x01, 0x00)       # verified read
WEAR_STATE = Prop(Feature.DEVICE, 0x02)                    # [left?, right?] 3 = worn, 2 = out (observed)
APTX_96KHZ = Prop(Feature.DEVICE, 0x06, 0x05)              # verified read, setter from code
APTX_LOSSLESS = Prop(Feature.DEVICE, 0x08, 0x07)           # verified read, setter from code

# ---- power (0x03)
AUTO_POWER_OFF = Prop(Feature.POWER, 0x01, 0x00)           # payload [timer=0, u16 seconds]; verified
CHARGER_STATE = Prop(Feature.POWER, 0x02)                  # [left, right] ChargerState
BATTERY = Prop(Feature.POWER, 0x03)                        # [left %, right %, case %]; verified

# ---- generic audio (0x04)
CODEC = Prop(Feature.GENERIC_AUDIO, 0x00)                  # [Codec]; verified
PROMPTS = Prop(Feature.GENERIC_AUDIO, 0x02, 0x01)          # [PromptsMode]
AUDIO_MODE = Prop(Feature.GENERIC_AUDIO, 0x04, 0x03)       # [0, AudioMode]
SIDETONE = Prop(Feature.GENERIC_AUDIO, 0x06, 0x05)         # [level 0..steps]
AUTO_ANSWER = Prop(Feature.GENERIC_AUDIO, 0x0B, 0x0A)
SMART_PAUSE = Prop(Feature.GENERIC_AUDIO, 0x0D, 0x0C)
COMFORT_CALL = Prop(Feature.GENERIC_AUDIO, 0x15, 0x14)
GAMING_MODE = Prop(Feature.GENERIC_AUDIO, 0x18, 0x17)      # verified set
SAMPLE_RATE = Prop(Feature.GENERIC_AUDIO, 0x1A)            # u32 Hz, 0 when idle

# ---- user EQ (0x08)
EQ_CONFIG = Prop(Feature.USER_EQ, 0x00)                    # [bands, min i8, max i8, ...] (0.1 dB)
EQ_BAND = Prop(Feature.USER_EQ, 0x02, 0x01)                # get [band] -> [gain]; set [band, gain i8]; notif = all bands
BASS_BOOST = Prop(Feature.USER_EQ, 0x09, 0x08)             # verified

# ---- versions (0x09)
HW_REVISION = Prop(Feature.VERSIONS, 0x00)
FW_VERSION = Prop(Feature.VERSIONS, 0x01)                  # 3 x u16
MODEL_ID = Prop(Feature.VERSIONS, 0x06)                    # NUL-terminated string

# ---- device management (0x0a)
PAIRED_COUNT = Prop(Feature.DEVICE_MANAGEMENT, 0x00)       # [?, count]
PAIRED_DEVICE = Prop(Feature.DEVICE_MANAGEMENT, 0x01)      # [index] -> [index, a, b, name\0]

# ---- touch / MMI (0x0b)
TOUCH_ACTION = Prop(Feature.MMI_CONFIG, 0x01, 0x00)        # get [btn, pattern] -> [btn, pattern, action]; set [btn, pattern, action]; verified
TOUCH_LOCKED = Prop(Feature.MMI_CONFIG, 0x07)

# ---- transparent hearing (0x0c)
TRANSPARENCY_LEVEL = Prop(Feature.TRANSPARENT_HEARING, 0x03, 0x02)  # 0..100; verified
TRANSPARENCY = Prop(Feature.TRANSPARENT_HEARING, 0x05, 0x04)        # on/off; verified

# ---- ANC (0x0d)
ANC_MODES = Prop(Feature.ANC, 0x01, 0x00)                  # get -> [(mode, value) x3]; set [mode, value]; verified for anti-wind
ANC = Prop(Feature.ANC, 0x05, 0x04)                        # on/off; verified
ANC_MODE_ANTI_WIND = 1
ANC_MODE_ADAPTIVE = 3

# ---- find device (0x16)
FIND_STOP = 0x01      # [side]
FIND_START = 0x02     # [side]   -- VERY LOUD
FIND_STATE = 0x03     # [side] -> notification [ringing, side]


class Side(IntEnum):
    RIGHT = 0   # verified: side 0 rang the right earbud
    LEFT = 1


class TouchPattern(IntEnum):
    TAP = 1
    DOUBLE_TAP = 2
    TRIPLE_TAP = 3
    LONG_PRESS = 4


class TouchAction(IntEnum):
    NONE = 0x00
    PLAY_PAUSE = 0x01
    NEXT_SONG = 0x02
    PREVIOUS_SONG = 0x03
    VOLUME_UP = 0x04
    VOLUME_DOWN = 0x05
    TRANSPARENT_HEARING = 0x06
    VOICE_ASSISTANT = 0x07
    POWER_OFF = 0x08
    ANC_ON_OFF = 0x09
    LAUNCH_COMPANION_APP = 0x0A
    ANC_ADAPTIVE_TOGGLE = 0x0B
    ANC_COMFORT_TOGGLE = 0x0C
    TOGGLE_TOUCH_LOCK = 0x0D
    FOCUS_NEXT_DEVICE = 0x0E
    TOGGLE_SOUND_MODE = 0x0F
    TOGGLE_GAMING_MODE = 0x11
    ANC_TH_TOGGLE = 0x12
    TOGGLE_ANTI_WIND = 0x14
    TOGGLE_BASS_BOOST = 0x15
    AURACAST_ON_OFF = 0x17


TOUCH_ACTION_LABELS = {
    TouchAction.NONE: "No action",
    TouchAction.PLAY_PAUSE: "Play / pause",
    TouchAction.NEXT_SONG: "Next track",
    TouchAction.PREVIOUS_SONG: "Previous track",
    TouchAction.VOLUME_UP: "Volume up",
    TouchAction.VOLUME_DOWN: "Volume down",
    TouchAction.TRANSPARENT_HEARING: "Transparent hearing",
    TouchAction.VOICE_ASSISTANT: "Voice assistant",
    TouchAction.POWER_OFF: "Power off",
    TouchAction.ANC_ON_OFF: "Noise cancelling on/off",
    TouchAction.LAUNCH_COMPANION_APP: "Launch companion app",
    TouchAction.ANC_ADAPTIVE_TOGGLE: "Adaptive ANC toggle",
    TouchAction.ANC_COMFORT_TOGGLE: "ANC comfort toggle",
    TouchAction.TOGGLE_TOUCH_LOCK: "Touch lock",
    TouchAction.FOCUS_NEXT_DEVICE: "Switch device",
    TouchAction.TOGGLE_SOUND_MODE: "Sound mode",
    TouchAction.TOGGLE_GAMING_MODE: "Gaming mode",
    TouchAction.ANC_TH_TOGGLE: "ANC / transparency toggle",
    TouchAction.TOGGLE_ANTI_WIND: "Anti-wind",
    TouchAction.TOGGLE_BASS_BOOST: "Bass boost",
    TouchAction.AURACAST_ON_OFF: "Auracast",
}


class Codec(IntEnum):
    SBC = 0x00
    AAC = 0x01
    APTX = 0x02
    APTX_LL = 0x03
    MP3 = 0x04
    APTX_HD = 0x05
    FASTSTREAM = 0x06
    LHDC = 0x07
    APTX_ADAPTIVE = 0x08
    APTX_LOSSLESS = 0x09
    LC3 = 0x0A
    USB = 0x0B
    APTX_VOICE = 0x0C
    APTX_LITE = 0x0D
    LC3_GAMING = 0x0E
    CALL_ONGOING = 0xF0
    UNKNOWN = 0xFE
    NO_DEVICE = 0xFF


CODEC_LABELS = {
    Codec.SBC: "SBC", Codec.AAC: "AAC", Codec.APTX: "aptX", Codec.APTX_LL: "aptX LL",
    Codec.MP3: "MP3", Codec.APTX_HD: "aptX HD", Codec.FASTSTREAM: "FastStream",
    Codec.LHDC: "LHDC", Codec.APTX_ADAPTIVE: "aptX Adaptive", Codec.APTX_LOSSLESS: "aptX Lossless",
    Codec.LC3: "LC3", Codec.USB: "USB", Codec.APTX_VOICE: "aptX Voice", Codec.APTX_LITE: "aptX Lite",
    Codec.LC3_GAMING: "LC3 Gaming", Codec.CALL_ONGOING: "Call", Codec.UNKNOWN: "Unknown",
    Codec.NO_DEVICE: "Not streaming",
}


class PromptsMode(IntEnum):
    OFF = 0
    TONES = 1
    TONES_AND_VOICE = 2


class AudioMode(IntEnum):
    OFF = 0
    USER_EQ = 1
    PODCAST = 2
    PERSONALIZED_SOUND = 3
    PARAMETRIC_EQ = 4
    HEARING_ENHANCEMENT = 5


class ChargerState(IntEnum):
    DISCONNECTED = 0
    CHARGING = 1
    COMPLETE = 2
