"""PySide6 desktop app for Sennheiser headphones: a tray menu plus a window for the detailed views."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea, QSlider, QStackedWidget,
                               QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget)

from . import discovery
from . import protocol as P
from .device import SennheiserDevice
from .theme import app_icon
from .tray import Tray, matching_preset

log = logging.getLogger(__name__)


class _Job(QRunnable):
    def __init__(self, fn, done, fail):
        super().__init__()
        self.fn, self.done, self.fail = fn, done, fail

    def run(self):
        try:
            self.fn()
            self.done.emit()
        except Exception as e:  # noqa: BLE001 – surface every error in the UI
            log.debug("job failed", exc_info=True)
            self.fail.emit(f"{type(e).__name__}: {e}")


class JobBridge(QObject):
    """Lives in the GUI thread; its slots run queued there when a worker emits."""
    done = Signal()
    failed = Signal(str)

    def __init__(self, ok, fail):
        super().__init__()
        self._ok_cb, self._fail_cb = ok, fail
        self.done.connect(self._ok)
        self.failed.connect(self._fail)

    @Slot()
    def _ok(self):
        self._ok_cb()

    @Slot(str)
    def _fail(self, msg):
        self._fail_cb(msg)


class _NotReady(Exception):
    pass


class Controller(QObject):
    """Owns the connection. All Bluetooth traffic runs serialized on one worker thread.

    changed(topic) carries the device topics (battery, noise, eq, …) plus:
      "connection" – a device was connected or lost (self.dev changed)
      "status"     – only the connection status text changed
      "sync"       – a command failed; views should re-read the real state
    """
    changed = Signal(str)
    error = Signal(str)
    _dev_event = Signal(object, str)

    RETRY_MS = 15_000

    def __init__(self, address: str | None = None, transport_factory=None):
        super().__init__()
        self.address = address.upper() if address else None
        self._transport_factory = transport_factory
        self.dev: SennheiserDevice | None = None
        self.connecting = False
        self.status = "Looking for headphones…"
        self.candidates: list[discovery.PairedHeadset] = []
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._bridges: set[JobBridge] = set()
        self._dev_event.connect(self._on_dev_event)
        self.retry = QTimer(self)
        self.retry.setInterval(self.RETRY_MS)
        self.retry.timeout.connect(lambda: self.connect_device(quiet=True))

    def _start(self, fn, ok, fail):
        b = JobBridge(lambda: (self._bridges.discard(b), ok()), lambda m: (self._bridges.discard(b), fail(m)))
        self._bridges.add(b)
        self.pool.start(_Job(fn, b.done, b.failed))

    def _set_status(self, text: str):
        if text != self.status:
            self.status = text
            self.changed.emit("status")

    def connect_device(self, address: str | None = None, quiet: bool = False):
        """quiet: background retry – only connects if BlueZ already has the headphones connected."""
        if self.connecting or self.dev:
            return
        if address:
            self.address = address
        self.connecting = True
        if not quiet:
            self.status = "Connecting…"
            self.changed.emit("status")
        res = {"addr": self.address}

        def work():
            res["cands"] = discovery.sennheiser_devices()
            addr = res["addr"] = res["addr"] or discovery.pick_device(devices=res["cands"])
            if not addr:
                raise _NotReady("No paired Sennheiser headphones found. Pair them in the system "
                                "Bluetooth settings first.")
            if quiet and not any(d.address == addr and d.connected for d in res["cands"]):
                raise _NotReady("Headphones not connected.")
            dev = SennheiserDevice(addr, transport=self._transport_factory(addr) if self._transport_factory else None)
            dev.add_listener(lambda topic: self._dev_event.emit(dev, topic))
            try:
                dev.connect()
            except Exception:
                dev.close()
                raise
            res["dev"] = dev

        def ok():
            self.connecting = False
            self.candidates = res["cands"]
            self.address = res["addr"]
            self.dev = res["dev"]
            self.retry.stop()
            cfg = discovery.load_config()
            cfg["last_device"] = self.address
            discovery.save_config(cfg)
            self.status = "Connected"
            self.changed.emit("connection")

        def fail(msg):
            self.connecting = False
            self.candidates = res.get("cands", self.candidates)
            self.address = res["addr"]
            if msg.startswith("_NotReady: "):
                msg = msg.removeprefix("_NotReady: ")
            elif self.address:
                msg = (f"Could not connect to {self.address}.\n{msg}\n\n"
                       "Make sure the earbuds are out of the case and connected.")
            self.retry.start()
            if quiet and self.status.startswith("Could not connect"):
                return      # keep showing the reason of the last explicit attempt
            self.status = msg
            self.changed.emit("status")
        self._start(work, ok, fail)

    def run(self, fn):
        """Run fn(device) on the worker thread."""
        dev = self.dev
        if dev:
            self._start(lambda: fn(dev), lambda: None, self._failed)

    def _failed(self, msg):
        self.error.emit(msg)
        self.changed.emit("sync")       # snap views back to the real state

    @Slot(object, str)
    def _on_dev_event(self, dev, topic):
        if dev is not self.dev:
            return
        if topic == "disconnected":
            self.dev = None
            dev.close()
            self.status = "Disconnected."
            self.retry.start()
            self.changed.emit("connection")
            return
        self.changed.emit(topic)

    def shutdown(self):
        self.retry.stop()
        self.pool.clear()
        if self.dev:
            self.dev.close()
        self.pool.waitForDone(3000)


def _clear_grid(grid: QGridLayout, from_row: int = 0) -> None:
    for i in reversed(range(grid.count())):
        r, _c, _rs, _cs = grid.getItemPosition(i)
        if r >= from_row:
            item = grid.takeAt(i)
            if item.widget():
                item.widget().deleteLater()


def _frame(title: str) -> tuple[QGroupBox, QVBoxLayout]:
    box = QGroupBox(title)
    lay = QVBoxLayout(box)
    return box, lay


class BatteryChip(QWidget):
    def __init__(self, label: str):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(label)
        self.title.setAlignment(Qt.AlignCenter)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setFixedWidth(90)
        self.bar.setFormat("%p%")
        lay.addWidget(self.title)
        lay.addWidget(self.bar)

    def set(self, pct, charging=False):
        if pct is None:
            self.bar.setFormat("–")
            self.bar.setValue(0)
        else:
            self.bar.setValue(int(pct))
            self.bar.setFormat(f"%p%{' ⚡' if charging else ''}")


class MainWindow(QMainWindow):
    """The detailed views: equalizer sliders, touch remapping, find earbuds, device info.
    Everyday controls live in the tray menu."""
    TABS = ("eq", "touch", "find", "device")

    def __init__(self, ctrl: Controller):
        super().__init__()
        self.setWindowTitle("Smart Control")
        self.setWindowIcon(app_icon())
        self.resize(560, 620)
        self.ctrl = ctrl
        self.hide_on_close = False
        self._updating = False
        ctrl.changed.connect(self.on_changed)
        ctrl.error.connect(lambda msg: self.statusBar().showMessage(msg, 6000))

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self._build_connect_page()
        self._build_main_page()
        self._show_connection()

    @property
    def dev(self) -> SennheiserDevice | None:
        return self.ctrl.dev

    # ------------------------------------------------------------------ window management
    def show_tab(self, tab: str | None):
        if tab in self.TABS:
            self.tabs.setCurrentIndex(self.TABS.index(tab))
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_tab(None)

    def closeEvent(self, e):
        if self.hide_on_close:
            e.ignore()
            self.hide()
        else:
            super().closeEvent(e)

    # ------------------------------------------------------------------ pages
    def _build_connect_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch()
        self.conn_label = QLabel()
        self.conn_label.setAlignment(Qt.AlignCenter)
        self.conn_label.setWordWrap(True)
        self.device_combo = QComboBox()
        self.retry_btn = QPushButton("Connect")
        self.retry_btn.clicked.connect(lambda: self.ctrl.connect_device(self.device_combo.currentData()))
        lay.addWidget(self.conn_label)
        lay.addWidget(self.device_combo)
        lay.addWidget(self.retry_btn, alignment=Qt.AlignCenter)
        lay.addStretch()
        self.stack.addWidget(w)

    def _build_main_page(self):
        w = QWidget()
        outer = QVBoxLayout(w)
        # header
        head = QHBoxLayout()
        names = QVBoxLayout()
        self.name_label = QLabel()
        self.name_label.setStyleSheet("font-size: 16pt; font-weight: 600;")
        self.sub_label = QLabel()
        self.sub_label.setStyleSheet("color: palette(placeholder-text);")
        names.addWidget(self.name_label)
        names.addWidget(self.sub_label)
        head.addLayout(names, 1)
        self.bat = {k: BatteryChip(t) for k, t in (("left", "Left"), ("right", "Right"), ("case", "Case"))}
        for chip in self.bat.values():
            head.addWidget(chip)
        outer.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._scroll(self._eq_tab()), "Equalizer")
        self.tabs.addTab(self._scroll(self._controls_tab()), "Touch controls")
        self.tabs.addTab(self._scroll(self._find_tab()), "Find earbuds")
        self.tabs.addTab(self._scroll(self._device_tab()), "Device")
        outer.addWidget(self.tabs, 1)
        self.stack.addWidget(w)

    @staticmethod
    def _scroll(inner: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setWidget(inner)
        return sa

    def _eq_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Preset"))
        self.preset_combo = QComboBox()
        self.preset_combo.activated.connect(self._preset_chosen)
        prow.addWidget(self.preset_combo, 1)
        lay.addLayout(prow)
        self.eq_grid = QGridLayout()
        lay.addLayout(self.eq_grid)
        self.eq_sliders: list[QSlider] = []
        self.eq_values: list[QLabel] = []
        self.bassboost = QCheckBox("Bass boost")
        self.bassboost.toggled.connect(lambda v: self._ui(lambda: self.ctrl.run(lambda d: d.set_bass_boost(v))))
        lay.addWidget(self.bassboost)
        lay.addStretch()
        return w

    def _controls_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("Choose what each gesture does on the left and right earbud.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.touch_grid = QGridLayout()
        self.touch_grid.addWidget(QLabel("<b>Gesture</b>"), 0, 0)
        self.touch_grid.addWidget(QLabel("<b>Left</b>"), 0, 1)
        self.touch_grid.addWidget(QLabel("<b>Right</b>"), 0, 2)
        lay.addLayout(self.touch_grid)
        self.touch_combos: dict[tuple[P.Side, P.TouchPattern], QComboBox] = {}
        lay.addStretch()
        return w

    def _find_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        warn = QLabel("⚠ The earbud plays a <b>very loud</b> sound. Only use this when it is out of your ear.")
        warn.setWordWrap(True)
        lay.addWidget(warn)
        row = QHBoxLayout()
        self.find_btns = {}
        for side in (P.Side.LEFT, P.Side.RIGHT):
            b = QPushButton(f"Ring {side.name.lower()}")
            b.setMinimumHeight(36)
            b.clicked.connect(lambda _=False, s=side: self._find(s))
            self.find_btns[side] = b
            row.addWidget(b)
        lay.addLayout(row)
        lay.addStretch()
        return w

    def _device_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        box, bl = _frame("Information")
        self.info_form = QFormLayout()
        self.info_labels = {}
        for key, text in (("model", "Model"), ("firmware", "Firmware"), ("serial", "Serial number"),
                          ("codec", "Audio"), ("address", "Bluetooth address")):
            lab = QLabel()
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.info_labels[key] = lab
            self.info_form.addRow(text, lab)
        bl.addLayout(self.info_form)
        lay.addWidget(box)
        box, bl = _frame("Paired devices")
        self.paired_list = QListWidget()
        self.paired_list.setMaximumHeight(130)
        bl.addWidget(self.paired_list)
        lay.addWidget(box)
        refresh = QPushButton("Refresh everything")
        refresh.clicked.connect(lambda: self.ctrl.run(lambda d: d.refresh()))
        lay.addWidget(refresh)
        lay.addStretch()
        return w

    # ------------------------------------------------------------------ connection
    def _show_connection(self):
        if self.dev:
            self.stack.setCurrentIndex(1)
            return
        self.conn_label.setText(self.ctrl.status)
        current = self.device_combo.currentData()
        self.device_combo.clear()
        for d in self.ctrl.candidates:
            self.device_combo.addItem(f"{d.name} ({d.address})", d.address)
        idx = self.device_combo.findData(current or self.ctrl.address)
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)
        self.device_combo.setVisible(self.device_combo.count() > 1)
        self.retry_btn.setEnabled(not self.ctrl.connecting)
        self.stack.setCurrentIndex(0)

    # ------------------------------------------------------------------ profile-dependent widgets
    def _populate_profile(self):
        prof = self.dev.profile
        self.preset_combo.clear()
        self.preset_combo.addItem("Custom")
        for name in prof.eq_presets:
            self.preset_combo.addItem(name)
        # EQ sliders
        _clear_grid(self.eq_grid)
        self.eq_sliders.clear()
        self.eq_values.clear()
        n = len(self.dev.state.eq_gains) or len(prof.eq_band_labels)
        lo, hi = self.dev.state.eq_range
        labels = prof.eq_band_labels or tuple(f"Band {i + 1}" for i in range(n))
        for i in range(n):
            val = QLabel("0.0")
            val.setAlignment(Qt.AlignCenter)
            sl = QSlider(Qt.Vertical)
            sl.setRange(int(lo * 10), int(hi * 10))
            sl.setSingleStep(5)
            sl.setPageStep(10)
            sl.setTickInterval(10)
            sl.setTickPosition(QSlider.TicksBothSides)
            sl.setMinimumHeight(220)
            sl.valueChanged.connect(lambda v, lab=val: lab.setText(f"{v / 10:+.1f}"))
            sl.sliderReleased.connect(lambda i=i, s=sl: self._eq_band_changed(i, s.value()))
            name = QLabel(labels[i] if i < len(labels) else f"Band {i + 1}")
            name.setAlignment(Qt.AlignCenter)
            self.eq_grid.addWidget(val, 0, i)
            self.eq_grid.addWidget(sl, 1, i, alignment=Qt.AlignHCenter)
            self.eq_grid.addWidget(name, 2, i)
            self.eq_sliders.append(sl)
            self.eq_values.append(val)
        self.bassboost.setVisible(prof.eq_bass_boost)
        # touch grid
        _clear_grid(self.touch_grid, from_row=1)
        self.touch_combos.clear()
        for r, pat in enumerate(prof.touch_patterns, start=1):
            self.touch_grid.addWidget(QLabel(pat.name.replace("_", " ").capitalize()), r, 0)
            for col, side in ((1, P.Side.LEFT), (2, P.Side.RIGHT)):
                cb = QComboBox()
                for act in prof.touch_actions.get(pat, ()):
                    cb.addItem(P.TOUCH_ACTION_LABELS[act], int(act))
                cb.activated.connect(lambda _i, s=side, p=pat, c=cb: self.ctrl.run(
                    lambda d: d.set_touch_action(s, p, P.TouchAction(c.currentData()))))
                self.touch_grid.addWidget(cb, r, col)
                self.touch_combos[(side, pat)] = cb
        find = self.dev.supports(P.Feature.FIND_DEVICE)
        self.tabs.setTabVisible(self.TABS.index("find"), find)

    # ------------------------------------------------------------------ UI -> device
    def _ui(self, fn):
        """Run fn only for user-initiated changes, not while we sync widgets from state."""
        if not self._updating:
            fn()

    def _eq_band_changed(self, band, value):
        self.preset_combo.setCurrentIndex(0)
        self.ctrl.run(lambda d: d.set_eq_band(band, value / 10))

    def _preset_chosen(self, idx):
        if idx <= 0:
            return
        gains = self.dev.profile.eq_presets[self.preset_combo.itemText(idx)]
        self.ctrl.run(lambda d: d.set_eq(gains))

    def _find(self, side):
        if self.dev.state.ringing.get(side):
            self.ctrl.run(lambda d: d.find_stop(side))
            return
        ans = QMessageBox.warning(
            self, "Ring earbud",
            f"The {side.name.lower()} earbud will play a VERY LOUD sound.\n\n"
            "Take it out of your ear and keep it away from anyone's ears.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if ans == QMessageBox.Yes:
            self.ctrl.run(lambda d: d.find_start(side))

    # ------------------------------------------------------------------ device -> UI
    @Slot(str)
    def on_changed(self, topic):
        if topic == "connection" and self.dev:
            self._populate_profile()
        if topic in ("connection", "status"):
            self._show_connection()
        if self.dev:
            self.refresh_all()

    def refresh_all(self):
        st = self.dev.state
        prof = self.dev.profile
        self._updating = True
        try:
            self.name_label.setText(st.variant or prof.name)
            self.sub_label.setText(f"{st.model_id}  ·  firmware {st.firmware}")
            for k, chip in self.bat.items():
                chip.set(st.battery.get(k), st.charging.get(k) == P.ChargerState.CHARGING)
            for i, g in enumerate(st.eq_gains[:len(self.eq_sliders)]):
                if not self.eq_sliders[i].isSliderDown():
                    self.eq_sliders[i].setValue(int(round(g * 10)))
                    self.eq_values[i].setText(f"{g:+.1f}")
            preset = matching_preset(prof, st.eq_gains)
            self.preset_combo.setCurrentIndex(max(self.preset_combo.findText(preset), 0) if preset else 0)
            if st.bass_boost is not None:
                self.bassboost.setChecked(st.bass_boost)
            for key, cb in self.touch_combos.items():
                act = st.touch.get(key)
                if act is not None:
                    i = cb.findData(int(act))
                    if i < 0:
                        cb.addItem(P.TOUCH_ACTION_LABELS.get(act, f"Action {int(act)}"), int(act))
                        i = cb.count() - 1
                    cb.setCurrentIndex(i)
            codec = P.CODEC_LABELS.get(st.codec, str(st.codec)) if st.codec is not None else "?"
            rate = f" @ {st.sample_rate / 1000:g} kHz" if st.sample_rate else ""
            for k, v in (("model", st.model_id), ("firmware", st.firmware), ("serial", st.serial),
                         ("codec", codec + rate), ("address", self.dev.address)):
                self.info_labels[k].setText(v)
            self.paired_list.clear()
            for d in st.paired:
                self.paired_list.addItem(d.name)
            for side, b in self.find_btns.items():
                b.setText(f"Stop {side.name.lower()}" if st.ringing.get(side) else f"Ring {side.name.lower()}")
        finally:
            self._updating = False


def _instance_name() -> str:
    return f"smartcontrol-{os.getuid()}"


def _wake_running_instance() -> bool:
    """If the app already runs, ask it to show its window and return True."""
    sock = QLocalSocket()
    sock.connectToServer(_instance_name())
    if not sock.waitForConnected(300):
        return False
    sock.write(b"show\n")
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="smartcontrol-gui", description="Tray app for Sennheiser headphones.")
    ap.add_argument("address", nargs="?", help="Bluetooth address (default: last used / only connected)")
    ap.add_argument("--show", action="store_true", help="open the window at start (default: tray only)")
    ap.add_argument("--tray-icon", choices=("auto", "white", "black"),
                    default=discovery.load_config().get("tray_icon", "auto"),
                    help="tray icon colour (default: auto – follows the panel background; "
                         "or set \"tray_icon\" in the config file)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args, qt_args = ap.parse_known_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("Smart Control")
    app.setDesktopFileName("smartcontrol")
    app.setWindowIcon(app_icon())
    if _wake_running_instance():
        return 0
    server = QLocalServer(app)
    QLocalServer.removeServer(_instance_name())
    server.listen(_instance_name())

    ctrl = Controller(args.address)
    win = MainWindow(ctrl)

    def wake():
        while server.hasPendingConnections():
            server.nextPendingConnection().deleteLater()
        win.show_tab(None)
    server.newConnection.connect(wake)

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        app.setQuitOnLastWindowClosed(False)
        win.hide_on_close = True
        tray = Tray(ctrl, win, app.quit, args.tray_icon)
        tray.icon.show()
        ctrl.error.connect(lambda msg: None if win.isVisible() else tray.notify(msg))
    else:
        log.warning("no system tray available; running as a normal window")
    if args.show or tray is None:
        win.show_tab(None)
    app.aboutToQuit.connect(ctrl.shutdown)
    ctrl.connect_device()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
