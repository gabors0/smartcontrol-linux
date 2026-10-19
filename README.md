# Smart Control for Linux

Control Sennheiser headphones from Linux, like the *Smart Control Plus* phone app.
It's built and tested for the **MOMENTUM True Wireless 4**; other Sennheiser models
that use the same Qualcomm GAIA protocol will partially work (generic profile).

Features (MTW4):

* battery for left/right/case, charging state
* noise control: noise cancelling / transparency / off, transparency level, anti-wind
* 5-band equalizer with the app's presets, bass boost
* touch-control remapping (tap, double, triple, long press, per earbud)
* smart pause, on-head detection, auto-answer, comfort call, gaming mode,
  aptX 96 kHz / Lossless, prompts, sidetone, podcast mode, auto power-off
* device info, paired devices, current codec / sample rate
* find my earbuds (with a loudness warning – it is *very* loud)

## Requirements

* BlueZ, with the headphones paired and connected (normal system Bluetooth settings)
* Python ≥ 3.11
* GUI: PySide6 (`sudo dnf install python3-pyside6` on Fedora)

No root needed. It talks to the earbuds over their RFCOMM "GAIA" channel, alongside
normal audio.

## Usage

### Tray app

```sh
python3 -m smartcontrol.gui            # or: smartcontrol-gui after installing
python3 -m smartcontrol.gui --show     # also open the window
```

The app runs as a tray icon. Right-click it for the everyday controls:

* battery for left/right/case
* noise cancelling, transparency or off, plus transparency level and anti-wind
* EQ preset, bass boost and gaming mode
* a *Settings* submenu with the other options: smart pause, on-head detection,
  auto-answer, comfort call, aptX, podcast mode, prompts, sidetone and auto power-off

Left-click the icon, or choose an entry at the bottom of the menu, to open the window
for the views that need more room:

* the equalizer sliders
* touch-control remapping
* find my earbuds
* device info and paired devices

Closing the window keeps the app in the tray; use *Quit* in the menu to exit. Starting
the app again just opens the running instance's window. If the earbuds disconnect, the
app reconnects by itself once BlueZ connects them again.

The tray shows an earbud icon in white on dark panels and black on light ones. It
reads the panel colour from the Plasma theme on KDE, assumes a dark top bar on GNOME,
and otherwise follows the system light/dark setting. It switches by itself when you
change themes. To force a colour, use `--tray-icon white|black`, or put
`"tray_icon": "white"` in `~/.config/smartcontrol/config.json`. The icon is dimmed while
the earbuds aren't connected.

### CLI

```sh
python3 -m smartcontrol devices
python3 -m smartcontrol status          # add --json for scripts
python3 -m smartcontrol noise anc       # anc | transparency | off
python3 -m smartcontrol transparency 75
python3 -m smartcontrol antiwind on
python3 -m smartcontrol eq rock         # or five gains: eq 2 0 -1 0 1.5
python3 -m smartcontrol bassboost on
python3 -m smartcontrol touch left long_press voice_assistant
python3 -m smartcontrol set gaming_mode off
python3 -m smartcontrol set auto_power_off 60
python3 -m smartcontrol monitor         # live notifications
```

The device is picked automatically: the last one used, otherwise the only connected
Sennheiser. Use `-d AA:BB:CC:DD:EE:FF` to choose one explicitly.

## Install

```sh
pip install --user -e .                  # provides `smartcontrol` and `smartcontrol-gui`
./packaging/install-desktop-entry.sh     # app-menu entry
./packaging/install-desktop-entry.sh --autostart   # ...and start in the tray at login
```

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

The tests run against an in-memory simulator built from values captured on a real MTW4.

## How it works

See [docs/PROTOCOL.md](docs/PROTOCOL.md). The command table was recovered from the
Flutter app's Dart AOT snapshot. A patched Blutter dumped the Dart dispatch table,
which resolves each command class's vendor, feature and PDU getters. Every
state-changing command marked "verified" there was also tested on real hardware.

Not affiliated with Sennheiser or Sonova. The icons in `smartcontrol/icons/` are original
artwork and contain no Sennheiser trademarks.
