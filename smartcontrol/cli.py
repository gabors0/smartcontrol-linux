"""Command line interface:  smartcontrol [-d ADDR] <command> ..."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from . import discovery
from . import protocol as P
from .device import SETTING_LABELS, SETTINGS, NoiseMode, SennheiserDevice

BOOL = {"on": True, "off": False, "true": True, "false": False, "1": True, "0": False, "yes": True, "no": False}


def _bool(s: str) -> bool:
    try:
        return BOOL[s.lower()]
    except KeyError:
        raise argparse.ArgumentTypeError("expected on/off")


def _enum_arg(cls):
    def parse(s: str):
        try:
            return cls[s.upper().replace("-", "_")]
        except KeyError:
            raise argparse.ArgumentTypeError(f"one of: {', '.join(m.name.lower() for m in cls)}")
    return parse


def _label(v) -> str:
    if v is None:
        return "?"
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, P.Codec):
        return P.CODEC_LABELS[v]
    if isinstance(v, P.TouchAction):
        return P.TOUCH_ACTION_LABELS[v]
    if hasattr(v, "name"):
        return v.name.lower()
    return str(v)


def print_status(dev: SennheiserDevice) -> None:
    st = dev.state
    b = st.battery
    print(f"{st.variant or dev.profile.name}  ({dev.address})")
    print(f"  model      {st.model_id}   firmware {st.firmware}   serial {st.serial}")
    chg = {k: (" ⚡" if v == P.ChargerState.CHARGING else "") for k, v in st.charging.items()}
    print(f"  battery    L {b['left']}%{chg.get('left', '')}   R {b['right']}%{chg.get('right', '')}   case {b['case']}%")
    print(f"  audio      {_label(st.codec)}" + (f" @ {st.sample_rate / 1000:g} kHz" if st.sample_rate else ""))
    mode = st.noise_mode.value if st.noise_mode else "?"
    print(f"  noise      {mode}   transparency level {st.transparency_level}   anti-wind {_label(st.anti_wind)}")
    if st.eq_gains:
        labels = dev.profile.eq_band_labels or [f"band{i}" for i in range(len(st.eq_gains))]
        print("  equalizer  " + "  ".join(f"{l} {g:+.1f}" for l, g in zip(labels, st.eq_gains))
              + f"   bass boost {_label(st.bass_boost)}")
    for name in dev.profile.settings:
        if name in st.settings:
            v = st.settings[name]
            suffix = " min" if name == "auto_power_off" else ""
            print(f"  {SETTING_LABELS[name]:<38} {_label(v)}{suffix}")
    if st.touch:
        print("  touch controls:")
        for pat in dev.profile.touch_patterns:
            l = st.touch.get((P.Side.LEFT, pat))
            r = st.touch.get((P.Side.RIGHT, pat))
            print(f"    {pat.name.lower():<12} L: {_label(l):<26} R: {_label(r)}")
    if st.paired:
        print("  paired devices: " + ", ".join(d.name for d in st.paired))


def state_json(dev: SennheiserDevice) -> dict:
    st = dev.state
    conv = lambda v: v.name.lower() if hasattr(v, "name") else v
    return {
        "address": dev.address, "variant": st.variant, "model": st.model_id, "firmware": st.firmware,
        "serial": st.serial, "battery": st.battery,
        "charging": {k: conv(v) for k, v in st.charging.items()},
        "noise_mode": st.noise_mode.value if st.noise_mode else None,
        "transparency_level": st.transparency_level, "anti_wind": st.anti_wind,
        "eq": st.eq_gains, "bass_boost": st.bass_boost,
        "settings": {k: conv(v) for k, v in st.settings.items()},
        "touch": {f"{conv(s)}_{conv(p)}": conv(a) for (s, p), a in st.touch.items()},
        "codec": conv(st.codec), "sample_rate": st.sample_rate,
        "paired": [d.name for d in st.paired],
    }


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="smartcontrol", description="Control Sennheiser headphones (MOMENTUM True Wireless 4, ...)")
    ap.add_argument("-d", "--device", help="Bluetooth address (default: last used / only connected)")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices", help="list paired Sennheiser headphones")
    s = sub.add_parser("status", help="show everything")
    s.add_argument("--json", action="store_true")
    sub.add_parser("battery")
    s = sub.add_parser("noise", help="noise control mode")
    s.add_argument("mode", choices=[m.value for m in NoiseMode])
    s = sub.add_parser("transparency", help="transparency level 0-100")
    s.add_argument("level", type=int)
    s = sub.add_parser("antiwind")
    s.add_argument("state", type=_bool)
    s = sub.add_parser("eq", help="equalizer: preset name, or 5 gains in dB")
    s.add_argument("values", nargs="*")
    s = sub.add_parser("bassboost")
    s.add_argument("state", type=_bool)
    s = sub.add_parser("touch", help="set a touch gesture: touch left|right tap|double_tap|triple_tap|long_press ACTION")
    s.add_argument("side", type=_enum_arg(P.Side))
    s.add_argument("pattern", type=_enum_arg(P.TouchPattern))
    s.add_argument("action", type=_enum_arg(P.TouchAction))
    s = sub.add_parser("set", help="change a setting: " + ", ".join(SETTINGS))
    s.add_argument("name", choices=list(SETTINGS))
    s.add_argument("value")
    s = sub.add_parser("find", help="make an earbud ring (VERY LOUD – take it out of your ear!)")
    s.add_argument("side", type=_enum_arg(P.Side))
    s.add_argument("action", choices=["start", "stop"], nargs="?", default="start")
    s.add_argument("--yes", action="store_true", help="skip the safety prompt")
    sub.add_parser("monitor", help="print state changes as they happen")
    return ap


def _setting_value(name: str, raw: str):
    _, codec = SETTINGS[name]
    if codec in ("bool", "podcast"):
        return _bool(raw)
    if codec in ("int", "minutes"):
        return int(raw)
    return _enum_arg(codec)(raw)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=[logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)],
                        format="%(levelname)s %(name)s: %(message)s")
    if args.cmd == "devices":
        for d in discovery.sennheiser_devices():
            print(f"{d.address}  {d.name}{'  (connected)' if d.connected else ''}")
        return 0
    addr = discovery.pick_device(args.device)
    if not addr:
        print("No Sennheiser headphones found. Pair them first, or pass -d ADDRESS.", file=sys.stderr)
        return 2
    dev = SennheiserDevice(addr)
    try:
        dev.connect()
    except OSError as e:
        print(f"Could not connect to {addr}: {e}", file=sys.stderr)
        return 1
    cfg = discovery.load_config()
    cfg["last_device"] = addr
    discovery.save_config(cfg)
    try:
        return _run(dev, args)
    except (ValueError, argparse.ArgumentTypeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        dev.close()


def _run(dev: SennheiserDevice, args) -> int:
    st = dev.state
    c = args.cmd
    if c == "status":
        if args.json:
            print(json.dumps(state_json(dev), indent=2))
        else:
            print_status(dev)
    elif c == "battery":
        b = st.battery
        print(f"L {b['left']}%  R {b['right']}%  case {b['case']}%")
    elif c == "noise":
        dev.set_noise_mode(args.mode)
        print(f"noise control: {st.noise_mode.value}")
    elif c == "transparency":
        dev.set_transparency_level(args.level)
        print(f"transparency level: {st.transparency_level}")
    elif c == "antiwind":
        dev.set_anti_wind(args.state)
        print(f"anti-wind: {_label(st.anti_wind)}")
    elif c == "eq":
        presets = {k.lower(): v for k, v in dev.profile.eq_presets.items()}
        if not args.values:
            print("presets: " + ", ".join(dev.profile.eq_presets))
        elif len(args.values) == 1 and args.values[0].lower() in presets:
            dev.set_eq(presets[args.values[0].lower()])
        else:
            dev.set_eq([float(v) for v in args.values])
        print("equalizer: " + " ".join(f"{g:+.1f}" for g in st.eq_gains))
    elif c == "bassboost":
        dev.set_bass_boost(args.state)
        print(f"bass boost: {_label(st.bass_boost)}")
    elif c == "touch":
        dev.set_touch_action(args.side, args.pattern, args.action)
        print(f"{args.side.name.lower()} {args.pattern.name.lower()}: {_label(st.touch[(args.side, args.pattern)])}")
    elif c == "set":
        dev.set_setting(args.name, _setting_value(args.name, args.value))
        print(f"{SETTING_LABELS[args.name]}: {_label(st.settings.get(args.name))}")
    elif c == "find":
        if args.action == "stop":
            dev.find_stop(args.side)
            return 0
        if not args.yes:
            print("WARNING: the earbud will ring VERY loudly. Make sure it is NOT in anyone's ear.")
            if input("Type 'ring' to continue: ").strip().lower() != "ring":
                return 1
        dev.find_start(args.side)
        print("Ringing – press Enter to stop.")
        try:
            input()
        finally:
            dev.find_stop(args.side)
    elif c == "monitor":
        dev.add_listener(lambda topic: print(time.strftime("%H:%M:%S"), topic, json.dumps(state_json(dev).get(
            {"battery": "battery", "noise": "noise_mode", "eq": "eq", "settings": "settings",
             "touch": "touch", "audio": "codec", "find": "address"}.get(topic, "address")))))
        print("Monitoring – Ctrl+C to quit")
        try:
            while dev.t.connected:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
