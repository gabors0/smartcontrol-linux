"""High-level Sennheiser device: keeps a state model in sync and exposes setters."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from . import protocol as P
from .gaia import VENDOR_QUALCOMM, VENDOR_SENNHEISER, Packet, PacketType
from .models import GENERIC, Profile, profile_for
from .transport import GaiaCommandError, GaiaTransport

log = logging.getLogger(__name__)


class NoiseMode(str, Enum):
    ANC = "anc"
    TRANSPARENCY = "transparency"
    OFF = "off"


@dataclass
class PairedDevice:
    index: int
    name: str
    connected: bool
    raw: bytes


@dataclass
class DeviceState:
    variant: str = ""
    model_id: str = ""
    serial: str = ""
    app_version: str = ""
    firmware: str = ""
    features: dict[int, int] = field(default_factory=dict)   # Sennheiser feature -> version
    battery: dict[str, int | None] = field(default_factory=lambda: {"left": None, "right": None, "case": None})
    charging: dict[str, P.ChargerState | None] = field(default_factory=lambda: {"left": None, "right": None})
    anc: bool | None = None
    transparency: bool | None = None
    transparency_level: int | None = None
    anti_wind: bool | None = None
    eq_gains: list[float] = field(default_factory=list)       # dB
    eq_range: tuple[float, float] = (-6.0, 6.0)
    bass_boost: bool | None = None
    touch: dict[tuple[P.Side, P.TouchPattern], P.TouchAction | int] = field(default_factory=dict)
    settings: dict[str, object] = field(default_factory=dict)
    codec: P.Codec | int | None = None
    sample_rate: int | None = None
    paired: list[PairedDevice] = field(default_factory=list)
    ringing: dict[P.Side, bool] = field(default_factory=dict)

    @property
    def noise_mode(self) -> NoiseMode | None:
        if self.transparency is None and self.anc is None:
            return None
        if self.transparency:
            return NoiseMode.TRANSPARENCY       # transparency wins over ANC on MTW4
        return NoiseMode.ANC if self.anc else NoiseMode.OFF


# setting name -> (property, codec) ; codec: "bool" | enum class | "int"
SETTINGS: dict[str, tuple[P.Prop, object]] = {
    "smart_pause": (P.SMART_PAUSE, "bool"),
    "on_head_detection": (P.ON_HEAD_DETECTION, "bool"),
    "auto_answer": (P.AUTO_ANSWER, "bool"),
    "comfort_call": (P.COMFORT_CALL, "bool"),
    "gaming_mode": (P.GAMING_MODE, "bool"),
    "aptx_96khz": (P.APTX_96KHZ, "bool"),
    "aptx_lossless": (P.APTX_LOSSLESS, "bool"),
    "prompts": (P.PROMPTS, P.PromptsMode),
    "sidetone": (P.SIDETONE, "int"),
    "podcast_mode": (P.AUDIO_MODE, "podcast"),
    "auto_power_off": (P.AUTO_POWER_OFF, "minutes"),
}

SETTING_LABELS = {
    "smart_pause": "Smart pause",
    "on_head_detection": "On-head detection",
    "auto_answer": "Auto-answer calls",
    "comfort_call": "Comfort call",
    "gaming_mode": "Gaming mode (low latency)",
    "aptx_96khz": "High resolution (aptX 24-bit/96 kHz)",
    "aptx_lossless": "aptX Lossless",
    "prompts": "Tones and voice prompts",
    "sidetone": "Sidetone",
    "podcast_mode": "Podcast mode",
    "auto_power_off": "Auto power-off",
}


def _enum(cls, v):
    try:
        return cls(v)
    except ValueError:
        return v


def _cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode(errors="replace")


class SennheiserDevice:
    def __init__(self, address: str, channel: int | None = None,
                 transport: GaiaTransport | None = None):
        self.address = address
        self.t = transport or GaiaTransport(address, channel)
        self.t.on_notification = self._on_notification
        self.t.on_disconnect = self._on_disconnect
        self.state = DeviceState()
        self.profile: Profile = GENERIC
        self._listeners: list[Callable[[str], None]] = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ plumbing
    def add_listener(self, cb: Callable[[str], None]) -> None:
        """cb(topic) is called (from the reader thread) whenever part of the state changes.
        topic is one of: info, battery, noise, eq, touch, settings, audio, paired, find, disconnected."""
        self._listeners.append(cb)

    def _emit(self, topic: str) -> None:
        for cb in list(self._listeners):
            try:
                cb(topic)
            except Exception:
                log.exception("listener failed")

    def _on_disconnect(self, err) -> None:
        self._emit("disconnected")

    def supports(self, feature: int) -> bool:
        return feature in self.state.features

    def _get(self, prop: P.Prop, payload: bytes = b"") -> bytes:
        data = self.t.request(VENDOR_SENNHEISER, prop.feature, prop.get, payload)
        self._apply(prop.feature, prop.get, data, payload)
        return data

    def _set(self, prop: P.Prop, payload: bytes) -> None:
        if prop.set is None:
            raise ValueError("read-only property")
        self.t.request(VENDOR_SENNHEISER, prop.feature, prop.set, payload)

    def _try(self, fn, *a):
        try:
            return fn(*a)
        except (GaiaCommandError, TimeoutError) as e:
            log.info("skipped: %s", e)
            return None

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> None:
        self.t.connect()
        st = self.state
        q = lambda pdu: self.t.request(VENDOR_QUALCOMM, P.Feature.CORE, pdu)
        st.variant = _cstr(self._try(q, P.CORE_GET_VARIANT) or b"")
        st.serial = _cstr(self._try(q, P.CORE_GET_SERIAL) or b"")
        st.app_version = _cstr(self._try(q, P.CORE_GET_APP_VERSION) or b"")
        feats = self.t.request(VENDOR_SENNHEISER, P.Feature.CORE, P.CORE_GET_SUPPORTED_FEATURES)
        st.features = {feats[i]: feats[i + 1] for i in range(1, len(feats) - 1, 2)}
        self._try(self._get, P.FW_VERSION)
        self._try(self._get, P.MODEL_ID)
        self.profile = profile_for(st.variant, st.model_id)
        self._emit("info")
        # Registering makes the device push the current value of every notifiable property.
        for f in (P.Feature.DEVICE, P.Feature.POWER, P.Feature.GENERIC_AUDIO, P.Feature.USER_EQ,
                  P.Feature.DEVICE_MANAGEMENT, P.Feature.MMI_CONFIG, P.Feature.TRANSPARENT_HEARING,
                  P.Feature.ANC, P.Feature.FIND_DEVICE):
            if self.supports(f):
                self._try(self.t.request, VENDOR_SENNHEISER, P.Feature.CORE,
                          P.CORE_REGISTER_NOTIFICATION, bytes([f]))
        self.refresh()

    def close(self) -> None:
        self.t.close()

    def refresh(self) -> None:
        """Explicitly read everything (not all properties are pushed on registration)."""
        for prop in (P.BATTERY, P.CHARGER_STATE, P.ANC, P.TRANSPARENCY, P.TRANSPARENCY_LEVEL,
                     P.ANC_MODES, P.BASS_BOOST, P.CODEC, P.SAMPLE_RATE):
            if self.supports(prop.feature):
                self._try(self._get, prop)
        if self.supports(P.Feature.USER_EQ):
            self._try(self._get, P.EQ_CONFIG)
            for b in range(len(self.state.eq_gains) or 5):
                self._try(self._get, P.EQ_BAND, bytes([b]))
        for name in self.profile.settings:
            prop, _ = SETTINGS[name]
            if self.supports(prop.feature):
                self._try(self._get, prop, b"\x00" if name == "auto_power_off" else b"")
        if self.supports(P.Feature.MMI_CONFIG):
            for side in P.Side:
                for pat in self.profile.touch_patterns:
                    self._try(self._get, P.TOUCH_ACTION, bytes([side, pat]))
        if self.supports(P.Feature.DEVICE_MANAGEMENT):
            self.refresh_paired()

    def refresh_paired(self) -> None:
        data = self._try(self.t.request, VENDOR_SENNHEISER, P.PAIRED_COUNT.feature, P.PAIRED_COUNT.get)
        if not data:
            return
        count = data[-1]
        out = []
        for i in range(count):
            d = self._try(self.t.request, VENDOR_SENNHEISER, P.PAIRED_DEVICE.feature,
                          P.PAIRED_DEVICE.get, bytes([i]))
            if d and len(d) >= 3:
                out.append(PairedDevice(d[0], _cstr(d[3:]), bool(d[2]), d))
        self.state.paired = out
        self._emit("paired")

    # ------------------------------------------------------------------ parsing
    def _on_notification(self, p: Packet) -> None:
        if p.vendor != VENDOR_SENNHEISER or p.type != PacketType.NOTIFICATION:
            return
        self._apply(p.feature, p.pdu, p.payload, None)

    def _apply(self, feature: int, pdu: int, d: bytes, req: bytes | None) -> None:
        st = self.state
        key = (feature, pdu)
        topic = None
        with self._lock:
            if key == (P.BATTERY.feature, P.BATTERY.get) and d:
                vals = list(d) + [None] * 3
                st.battery = {"left": vals[0], "right": vals[1], "case": vals[2]}
                topic = "battery"
            elif key == (P.CHARGER_STATE.feature, P.CHARGER_STATE.get) and len(d) >= 2:
                st.charging = {"left": _enum(P.ChargerState, d[0]), "right": _enum(P.ChargerState, d[1])}
                topic = "battery"
            elif key == (P.ANC.feature, P.ANC.get) and d:
                st.anc = bool(d[0]); topic = "noise"
            elif key == (P.TRANSPARENCY.feature, P.TRANSPARENCY.get) and d:
                st.transparency = bool(d[0]); topic = "noise"
            elif key == (P.TRANSPARENCY_LEVEL.feature, P.TRANSPARENCY_LEVEL.get) and d:
                st.transparency_level = d[0]; topic = "noise"
            elif key == (P.ANC_MODES.feature, P.ANC_MODES.get) and len(d) >= 2:
                modes = {d[i]: d[i + 1] for i in range(0, len(d) - 1, 2)}
                if P.ANC_MODE_ANTI_WIND in modes:
                    st.anti_wind = bool(modes[P.ANC_MODE_ANTI_WIND])
                topic = "noise"
            elif key == (P.EQ_CONFIG.feature, P.EQ_CONFIG.get) and len(d) >= 3:
                n = d[0]
                st.eq_range = (int.from_bytes(d[1:2], "big", signed=True) / 10,
                               int.from_bytes(d[2:3], "big", signed=True) / 10)
                if len(st.eq_gains) != n:
                    st.eq_gains = [0.0] * n
                topic = "eq"
            elif key == (P.EQ_BAND.feature, P.EQ_BAND.get) and d:
                gains = [int.from_bytes(bytes([b]), "big", signed=True) / 10 for b in d]
                if req:           # response to get [band] -> [gain]
                    band = req[0]
                    while len(st.eq_gains) <= band:
                        st.eq_gains.append(0.0)
                    st.eq_gains[band] = gains[0]
                else:             # notification: all bands
                    st.eq_gains = gains
                topic = "eq"
            elif key == (P.BASS_BOOST.feature, P.BASS_BOOST.get) and d:
                st.bass_boost = bool(d[0]); topic = "eq"
            elif key == (P.TOUCH_ACTION.feature, P.TOUCH_ACTION.get) and len(d) >= 3:
                st.touch[(_enum(P.Side, d[0]), _enum(P.TouchPattern, d[1]))] = _enum(P.TouchAction, d[2])
                topic = "touch"
            elif key == (P.CODEC.feature, P.CODEC.get) and d:
                st.codec = _enum(P.Codec, d[0]); topic = "audio"
            elif key == (P.SAMPLE_RATE.feature, P.SAMPLE_RATE.get) and d:
                st.sample_rate = int.from_bytes(d[:4], "big"); topic = "audio"
            elif key == (P.FW_VERSION.feature, P.FW_VERSION.get) and len(d) >= 6:
                st.firmware = ".".join(str(int.from_bytes(d[i:i + 2], "big")) for i in (0, 2, 4))
                topic = "info"
            elif key == (P.MODEL_ID.feature, P.MODEL_ID.get):
                st.model_id = _cstr(d); topic = "info"
            elif feature == P.Feature.FIND_DEVICE and pdu == P.FIND_STATE and len(d) >= 2:
                st.ringing[_enum(P.Side, d[1])] = bool(d[0]); topic = "find"
            else:
                for name, (prop, codec) in SETTINGS.items():
                    if key == (prop.feature, prop.get) and d:
                        st.settings[name] = self._decode_setting(codec, d)
                        topic = "settings"
                        break
        if topic:
            self._emit(topic)

    @staticmethod
    def _decode_setting(codec, d: bytes):
        if codec == "bool":
            return bool(d[0])
        if codec == "int":
            return d[0]
        if codec == "podcast":
            return d[-1] == P.AudioMode.PODCAST
        if codec == "minutes":
            return int.from_bytes(d[1:3], "big") // 60 if len(d) >= 3 else None
        return _enum(codec, d[0])

    # ------------------------------------------------------------------ setters
    def set_noise_mode(self, mode: NoiseMode | str) -> None:
        mode = NoiseMode(mode)
        if mode == NoiseMode.TRANSPARENCY:
            self._set(P.TRANSPARENCY, b"\x01")
        elif mode == NoiseMode.ANC:
            self._set(P.TRANSPARENCY, b"\x00")
            self._set(P.ANC, b"\x01")
        else:
            self._set(P.TRANSPARENCY, b"\x00")
            self._set(P.ANC, b"\x00")
        self._get(P.ANC)
        self._get(P.TRANSPARENCY)

    def set_transparency_level(self, level: int) -> None:
        level = max(0, min(100, int(level)))
        self._set(P.TRANSPARENCY_LEVEL, bytes([level]))
        self._get(P.TRANSPARENCY_LEVEL)

    def set_anti_wind(self, on: bool) -> None:
        self._set(P.ANC_MODES, bytes([P.ANC_MODE_ANTI_WIND, 1 if on else 0]))
        self._get(P.ANC_MODES)

    def set_eq_band(self, band: int, db: float) -> None:
        lo, hi = self.state.eq_range
        db = max(lo, min(hi, float(db)))
        self._set(P.EQ_BAND, bytes([band]) + int(round(db * 10)).to_bytes(1, "big", signed=True))
        self._get(P.EQ_BAND, bytes([band]))

    def set_eq(self, gains) -> None:
        for band, db in enumerate(gains):
            if band >= len(self.state.eq_gains) or abs(self.state.eq_gains[band] - db) > 0.04:
                self.set_eq_band(band, db)

    def set_bass_boost(self, on: bool) -> None:
        self._set(P.BASS_BOOST, b"\x01" if on else b"\x00")
        self._get(P.BASS_BOOST)

    def set_touch_action(self, side: P.Side, pattern: P.TouchPattern, action: P.TouchAction) -> None:
        allowed = self.profile.touch_actions.get(P.TouchPattern(pattern))
        if allowed is not None and P.TouchAction(action) not in allowed:
            raise ValueError(f"{P.TouchAction(action).name} is not allowed for {P.TouchPattern(pattern).name}")
        self._set(P.TOUCH_ACTION, bytes([side, pattern, action]))
        self._get(P.TOUCH_ACTION, bytes([side, pattern]))

    def set_setting(self, name: str, value) -> None:
        prop, codec = SETTINGS[name]
        if codec == "bool":
            payload = b"\x01" if value else b"\x00"
        elif codec == "int":
            v = int(value)
            if name == "sidetone" and self.profile.sidetone_steps and not 0 <= v <= self.profile.sidetone_steps:
                raise ValueError(f"sidetone must be 0..{self.profile.sidetone_steps}")
            payload = bytes([v])
        elif codec == "podcast":
            payload = bytes([0, P.AudioMode.PODCAST if value else P.AudioMode.USER_EQ])
        elif codec == "minutes":
            payload = bytes([0]) + (int(value) * 60).to_bytes(2, "big")
        else:
            payload = bytes([int(codec(value) if not isinstance(value, codec) else value)])
        self._set(prop, payload)
        self._get(prop, b"\x00" if codec == "minutes" else b"")

    def find_start(self, side: P.Side) -> None:
        """Make an earbud ring. WARNING: extremely loud – never while worn."""
        self.t.request(VENDOR_SENNHEISER, P.Feature.FIND_DEVICE, P.FIND_START, bytes([side]))

    def find_stop(self, side: P.Side) -> None:
        self.t.request(VENDOR_SENNHEISER, P.Feature.FIND_DEVICE, P.FIND_STOP, bytes([side]))
