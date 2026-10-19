"""System-tray icon whose menu holds the everyday controls."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QActionGroup, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import protocol as P
from .device import SETTING_LABELS, SETTINGS, DeviceState, NoiseMode
from .models import Profile
from .theme import TrayThemeWatcher, tray_icon

if TYPE_CHECKING:
    from .gui import Controller, MainWindow

NOISE_LABELS = {NoiseMode.ANC: "Noise cancelling", NoiseMode.TRANSPARENCY: "Transparency",
                NoiseMode.OFF: "Noise control off"}
PROMPT_LABELS = {P.PromptsMode.OFF: "Off", P.PromptsMode.TONES: "Tones only",
                 P.PromptsMode.TONES_AND_VOICE: "Tones and voice prompts"}


def minutes_label(m: int) -> str:
    return f"{m} minutes" if m < 60 else f"{m // 60} hour{'s' if m > 60 else ''}"


def battery_text(st: DeviceState) -> str:
    parts = []
    for key, name in (("left", "Left"), ("right", "Right"), ("case", "Case")):
        pct = st.battery.get(key)
        if pct is not None:
            bolt = " ⚡" if st.charging.get(key) == P.ChargerState.CHARGING else ""
            parts.append(f"{name} {pct}%{bolt}")
    return " · ".join(parts) or "Battery unknown"


def matching_preset(prof: Profile, gains: list[float]) -> str | None:
    if not gains:
        return None
    return next((name for name, v in prof.eq_presets.items()
                 if all(abs(a - b) < 0.05 for a, b in zip(v, gains))), None)


class Tray(QObject):
    def __init__(self, ctrl: Controller, window: MainWindow, quit_app: Callable[[], None],
                 icon_style: str = "auto"):
        """icon_style: "white", "black", or "auto" to follow the panel background."""
        super().__init__()
        self.ctrl = ctrl
        self.window = window
        self._quit = quit_app
        self.menu: QMenu | None = None
        self.icon = QSystemTrayIcon(self)
        if icon_style == "auto":
            self._theme = TrayThemeWatcher(self)
            self._theme.changed.connect(self._set_icons)
            self._set_icons(self._theme.dark)
        else:
            self._set_icons(icon_style == "white")
        self.icon.activated.connect(self._activated)
        ctrl.changed.connect(self._on_changed)
        self._rebuild()

    @Slot(bool)
    def _set_icons(self, white: bool):
        self._icon = tray_icon(white)
        self._icon_off = tray_icon(white, opacity=0.45)     # not connected
        if self.menu:
            self.sync()

    # ------------------------------------------------------------------ building
    @staticmethod
    def _radio(menu: QMenu, items, on_pick) -> dict:
        group = QActionGroup(menu)
        acts = {}
        for label, value in items:
            a = menu.addAction(label)
            a.setCheckable(True)
            group.addAction(a)
            a.triggered.connect(lambda _=False, v=value: on_pick(v))
            acts[value] = a
        return acts

    @staticmethod
    def _toggle(menu: QMenu, label: str, on_set):
        a = menu.addAction(label)
        a.setCheckable(True)
        a.triggered.connect(on_set)
        return a

    def _run(self, fn):
        self.ctrl.run(fn)

    def _rebuild(self):
        old, self.menu = self.menu, QMenu()
        m = self.menu
        self.noise_actions: dict[NoiseMode, object] = {}
        self.tr_menu = self.eq_menu = self.custom_eq = None
        self.tr_actions: dict[int, object] = {}
        self.preset_actions: dict[str, object] = {}
        self.antiwind = self.bassboost = self.connect_action = None
        self.setting_actions: dict[str, object] = {}   # name -> QAction, or value -> QAction for choices

        self.title = m.addAction("Smart Control")
        self.title.setEnabled(False)
        self.status = m.addAction("")
        self.status.setEnabled(False)
        m.addSeparator()
        if self.ctrl.dev:
            self._build_device(m)
        else:
            self.connect_action = m.addAction("Connect")
            self.connect_action.triggered.connect(lambda: self.ctrl.connect_device())
            m.addAction("Open Smart Control…").triggered.connect(lambda: self.window.show_tab(None))
        m.addSeparator()
        m.addAction(QIcon.fromTheme("application-exit"), "Quit").triggered.connect(self._quit)
        self.icon.setContextMenu(m)
        if old:
            old.deleteLater()
        self.sync()

    def _build_device(self, m: QMenu):
        dev = self.ctrl.dev
        prof = dev.profile
        if dev.supports(P.Feature.ANC):
            modes = [NoiseMode.ANC]
            if dev.supports(P.Feature.TRANSPARENT_HEARING):
                modes.append(NoiseMode.TRANSPARENCY)
            modes.append(NoiseMode.OFF)
            self.noise_actions = self._radio(m, [(NOISE_LABELS[k], k) for k in modes],
                                             lambda mode: self._run(lambda d: d.set_noise_mode(mode)))
        if dev.supports(P.Feature.TRANSPARENT_HEARING):
            self.tr_menu = m.addMenu("Transparency level")
            self.tr_actions = self._radio(self.tr_menu, [(f"{s}%", s) for s in prof.transparency_steps],
                                          lambda lvl: self._run(lambda d: d.set_transparency_level(lvl)))
        if prof.anti_wind:
            self.antiwind = self._toggle(m, "Anti-wind", lambda on: self._run(lambda d: d.set_anti_wind(on)))
        m.addSeparator()

        if dev.supports(P.Feature.USER_EQ):
            self.eq_menu = m.addMenu("Equalizer")
            acts = self._radio(self.eq_menu, [(n, n) for n in prof.eq_presets] + [("Custom", None)],
                               self._preset)
            self.custom_eq = acts.pop(None)
            self.custom_eq.setEnabled(False)
            self.preset_actions = acts
            self.eq_menu.addSeparator()
            self.eq_menu.addAction("Edit equalizer…").triggered.connect(lambda: self.window.show_tab("eq"))
        if prof.eq_bass_boost:
            self.bassboost = self._toggle(m, "Bass boost", lambda on: self._run(lambda d: d.set_bass_boost(on)))
        if "gaming_mode" in prof.settings:
            self.setting_actions["gaming_mode"] = self._toggle(m, SETTING_LABELS["gaming_mode"],
                                                               self._setter("gaming_mode"))

        rest = [n for n in prof.settings if n != "gaming_mode"]
        if rest:
            sm = m.addMenu("Settings")
            for name in rest:
                if SETTINGS[name][1] in ("bool", "podcast"):
                    self.setting_actions[name] = self._toggle(sm, SETTING_LABELS[name], self._setter(name))
            sm.addSeparator()
            for name in rest:
                items = self._choices(name, prof)
                if items is not None:
                    sub = sm.addMenu("Prompts" if name == "prompts" else SETTING_LABELS[name])
                    self.setting_actions[name] = self._radio(sub, items, self._setter(name))
        m.addSeparator()
        m.addAction("Touch controls…").triggered.connect(lambda: self.window.show_tab("touch"))
        if dev.supports(P.Feature.FIND_DEVICE):
            m.addAction("Find my earbuds…").triggered.connect(lambda: self.window.show_tab("find"))
        m.addAction("Device info…").triggered.connect(lambda: self.window.show_tab("device"))

    @staticmethod
    def _choices(name: str, prof: Profile):
        if name == "prompts":
            return [(t, m) for m, t in PROMPT_LABELS.items()]
        if name == "sidetone":
            n = prof.sidetone_steps or 4
            return [("Off" if v == 0 else "Max" if v == n else str(v), v) for v in range(n + 1)]
        if name == "auto_power_off":
            return [(minutes_label(v), v) for v in prof.auto_power_off_minutes]
        return None

    def _setter(self, name: str):
        return lambda value: self._run(lambda d: d.set_setting(name, value))

    def _preset(self, name):
        gains = self.ctrl.dev.profile.eq_presets[name]
        self._run(lambda d: d.set_eq(gains))

    # ------------------------------------------------------------------ device -> menu
    @Slot(str)
    def _on_changed(self, topic: str):
        if topic == "connection":
            self._rebuild()
        else:
            self.sync()

    def sync(self):
        dev = self.ctrl.dev
        if not dev:
            self.title.setText("Smart Control")
            self.status.setText(self.ctrl.status)
            self.connect_action.setEnabled(not self.ctrl.connecting)
            self.icon.setIcon(self._icon_off)
            self.icon.setToolTip(f"Smart Control\n{self.ctrl.status}")
            return
        st, prof = dev.state, dev.profile
        name = st.variant or prof.name
        mode = st.noise_mode
        self.title.setText(name)
        self.status.setText(battery_text(st))
        self.icon.setIcon(self._icon)
        tip = [name, battery_text(st)]
        if mode is not None:
            tip.append(NOISE_LABELS[mode])
        self.icon.setToolTip("\n".join(tip))

        if mode in self.noise_actions:
            self.noise_actions[mode].setChecked(True)
        if self.tr_menu:
            self.tr_menu.setEnabled(mode == NoiseMode.TRANSPARENCY)
            if st.transparency_level is not None:
                steps = prof.transparency_steps
                near = min(steps, key=lambda s: abs(s - st.transparency_level))
                self.tr_actions[near].setChecked(True)
                self.tr_menu.setTitle(f"Transparency level ({st.transparency_level}%)")
        if self.antiwind:
            self.antiwind.setEnabled(mode == NoiseMode.ANC)
            self.antiwind.setChecked(bool(st.anti_wind))
        if self.eq_menu:
            preset = matching_preset(prof, st.eq_gains)
            (self.preset_actions.get(preset) or self.custom_eq).setChecked(True)
            self.eq_menu.setTitle(f"Equalizer: {preset or 'Custom'}")
        if self.bassboost:
            self.bassboost.setChecked(bool(st.bass_boost))
        for name, act in self.setting_actions.items():
            v = st.settings.get(name)
            if v is None:
                continue
            if isinstance(act, dict):
                if v in act:
                    act[v].setChecked(True)
            else:
                act.setChecked(bool(v))

    @Slot(QSystemTrayIcon.ActivationReason)
    def _activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.window.toggle()

    def notify(self, msg: str):
        self.icon.showMessage("Smart Control", msg, QSystemTrayIcon.Warning, 6000)
