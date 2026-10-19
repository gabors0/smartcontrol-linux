import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QEventLoop
    from PySide6.QtWidgets import QApplication
except ImportError:  # GUI extra not installed
    QApplication = None

from smartcontrol import protocol as P
from smartcontrol.device import NoiseMode
from tests.fake_device import FakeMTW4

ADDR = "00:00:00:00:00:00"


def wait_until(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        QCoreApplication.processEvents(QEventLoop.AllEvents, 20)
        time.sleep(0.005)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class TrayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from smartcontrol import discovery
        from smartcontrol.gui import Controller, MainWindow
        from smartcontrol.tray import Tray
        for name, value in (("sennheiser_devices", []), ("load_config", {}), ("save_config", None)):
            p = mock.patch.object(discovery, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.fake = FakeMTW4()
        self.ctrl = Controller(ADDR, transport_factory=lambda _a: self.fake)
        self.win = MainWindow(self.ctrl)
        self.tray = Tray(self.ctrl, self.win, lambda: None)
        self.addCleanup(self.ctrl.shutdown)
        self.ctrl.connect_device()
        wait_until(lambda: self.ctrl.dev is not None)

    def test_menu_shows_state(self):
        self.assertEqual(self.tray.title.text(), "MOMENTUM TW 4")
        self.assertEqual(self.tray.status.text(), "Left 70% · Right 80% · Case 60%")
        self.assertTrue(self.tray.noise_actions[NoiseMode.TRANSPARENCY].isChecked())
        self.assertTrue(self.tray.tr_actions[50].isChecked())
        self.assertTrue(self.tray.tr_menu.isEnabled())
        self.assertFalse(self.tray.antiwind.isEnabled())       # only meaningful with ANC
        self.assertTrue(self.tray.preset_actions["Flat"].isChecked())
        self.assertTrue(self.tray.setting_actions["gaming_mode"].isChecked())
        self.assertTrue(self.tray.setting_actions["sidetone"][2].isChecked())
        self.assertTrue(self.tray.setting_actions["auto_power_off"][30].isChecked())
        self.assertTrue(self.tray.setting_actions["prompts"][P.PromptsMode.TONES].isChecked())

    def test_noise_mode_from_menu(self):
        self.tray.noise_actions[NoiseMode.ANC].trigger()
        wait_until(lambda: self.tray.antiwind.isEnabled())
        self.assertEqual(self.fake.v[(0xD, 5)], b"\x01")
        self.assertTrue(self.tray.noise_actions[NoiseMode.ANC].isChecked())
        self.assertTrue(self.tray.antiwind.isEnabled())
        self.assertFalse(self.tray.tr_menu.isEnabled())

    def test_preset_from_menu(self):
        self.tray.preset_actions["Rock"].trigger()
        wait_until(lambda: self.fake.eq == [-25, 5, 25, -5, -35])
        wait_until(lambda: self.tray.eq_menu.title() == "Equalizer: Rock")
        self.assertEqual(self.win.preset_combo.currentText(), "Rock")

    def test_settings_from_menu(self):
        self.tray.setting_actions["smart_pause"].trigger()       # on -> off
        self.tray.setting_actions["auto_power_off"][60].trigger()
        wait_until(lambda: self.fake.apo == 3600)
        self.assertEqual(self.fake.v[(4, 0x0D)], b"\x00")
        wait_until(lambda: self.ctrl.dev.state.settings["auto_power_off"] == 60)

    def test_window_has_detail_views(self):
        self.assertEqual(self.win.stack.currentIndex(), 1)
        self.assertEqual(len(self.win.eq_sliders), 5)
        self.assertEqual(len(self.win.touch_combos), 8)
        self.assertEqual(self.win.info_labels["firmware"].text(), "5.22.1")
        self.win.show_tab("find")
        self.assertEqual(self.win.tabs.currentIndex(), 2)
        self.win.hide()

    def test_disconnect_rebuilds_menu(self):
        self.fake.on_disconnect(None)
        wait_until(lambda: self.ctrl.dev is None)
        self.assertEqual(self.tray.status.text(), "Disconnected.")
        self.assertIsNotNone(self.tray.connect_action)
        self.assertEqual(self.tray.noise_actions, {})
        self.assertTrue(self.ctrl.retry.isActive())
        self.assertEqual(self.win.stack.currentIndex(), 0)

    def test_tray_icon_follows_panel_theme(self):
        from smartcontrol.theme import ICONS
        self.assertTrue((ICONS / "tray-earbuds-white.svg").exists())
        before = self.tray.icon.icon().cacheKey()
        self.tray._theme.changed.emit(not self.tray._theme.dark)
        self.assertNotEqual(self.tray.icon.icon().cacheKey(), before)
        self.assertFalse(self.tray.icon.icon().isNull())


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class ThemeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def detect(self, desktop, plasmarc=None, kdeglobals=None):
        import tempfile
        from pathlib import Path
        from smartcontrol import theme
        with tempfile.TemporaryDirectory() as d:
            if plasmarc is not None:
                Path(d, "plasmarc").write_text(plasmarc)
            if kdeglobals is not None:
                Path(d, "kdeglobals").write_text(kdeglobals)
            with mock.patch.object(theme, "_CONFIG_HOME", Path(d)), \
                    mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": desktop}):
                return theme.tray_background_dark()

    def test_kde_follows_colour_scheme(self):
        dark = "[General]\nfoo=1\n[Colors:Window]\nBackgroundNormal=32,35,38\n"
        light = "[Colors:Window]\nBackgroundAlternate=1,1,1\nBackgroundNormal=239,240,241\n[WM]\n"
        self.assertTrue(self.detect("KDE", kdeglobals=dark))
        self.assertFalse(self.detect("KDE", kdeglobals=light))

    def test_kde_explicit_plasma_style_wins(self):
        light = "[Colors:Window]\nBackgroundNormal=239,240,241\n"
        self.assertTrue(self.detect("KDE", plasmarc="[Theme]\nname=breeze-dark\n", kdeglobals=light))
        self.assertFalse(self.detect("KDE", plasmarc="[Theme]\nname=breeze-light\n",
                                     kdeglobals="[Colors:Window]\nBackgroundNormal=0,0,0\n"))

    def test_gnome_top_bar_is_dark(self):
        self.assertTrue(self.detect("ubuntu:GNOME"))


if __name__ == "__main__":
    unittest.main()
