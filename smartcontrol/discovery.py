"""Find paired Sennheiser headphones through BlueZ (bluetoothctl) and remember the last one."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

KNOWN_NAMES = re.compile(r"MOMENTUM|ACCENTUM|SENNHEISER|\bCX\b|MTW|HDB|PXC|IE \d+", re.I)
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "smartcontrol" / "config.json"


@dataclass
class PairedHeadset:
    address: str
    name: str
    connected: bool


def paired_devices() -> list[PairedHeadset]:
    try:
        out = subprocess.run(["bluetoothctl", "devices", "Paired"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    devs = []
    for line in out.splitlines():
        m = re.match(r"Device ([0-9A-F:]{17}) (.*)", line.strip())
        if not m:
            continue
        addr, name = m.groups()
        try:
            info = subprocess.run(["bluetoothctl", "info", addr], capture_output=True,
                                  text=True, timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            info = ""
        devs.append(PairedHeadset(addr, name, "Connected: yes" in info))
    return devs


def sennheiser_devices() -> list[PairedHeadset]:
    return [d for d in paired_devices() if KNOWN_NAMES.search(d.name)]


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))


def pick_device(address: str | None = None, devices: list[PairedHeadset] | None = None) -> str | None:
    """Explicit address > last used > the only connected Sennheiser > the only paired one."""
    if address:
        return address.upper()
    last = load_config().get("last_device")
    devs = sennheiser_devices() if devices is None else devices
    if last and any(d.address == last for d in devs):
        return last
    connected = [d for d in devs if d.connected]
    if len(connected) == 1:
        return connected[0].address
    if len(devs) == 1:
        return devs[0].address
    return last
