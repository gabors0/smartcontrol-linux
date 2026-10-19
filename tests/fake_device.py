"""In-memory MTW4 simulator implementing the GaiaTransport interface used by SennheiserDevice."""
from smartcontrol import protocol as P
from smartcontrol.gaia import (VENDOR_QUALCOMM, VENDOR_SENNHEISER, GaiaError, Packet,
                               PacketType, command_id)
from smartcontrol.transport import GaiaCommandError

S = VENDOR_SENNHEISER


class FakeMTW4:
    def __init__(self):
        self.on_notification = None
        self.on_disconnect = None
        self.connected = False
        self.registered = set()
        self.sent = []          # (vendor, feature, pdu, payload) log
        self.v = {              # (feature, get pdu) -> payload, values captured from a real MTW4
            (3, 3): bytes([70, 80, 60]), (3, 2): bytes([0, 0]),
            (0xD, 5): b"\x01", (0xC, 5): b"\x01", (0xC, 3): bytes([50]),
            (0xD, 1): bytes([1, 1, 2, 0, 3, 0]),
            (8, 9): b"\x00", (4, 0): b"\x01", (4, 0x1A): bytes.fromhex("0000bb80"),
            (9, 1): bytes.fromhex("000500160001"), (9, 6): b"MTW4 Graphite\x00",
            (4, 0x0D): b"\x01", (2, 1): b"\x01", (4, 0x0B): b"\x01", (4, 0x15): b"\x01",
            (4, 0x18): b"\x01", (2, 6): b"\x01", (2, 8): b"\x00", (4, 2): b"\x01",
            (4, 6): b"\x02", (4, 4): b"\x00\x01",
        }
        self.eq = [0] * 5
        self.touch = {(0, 1): 1, (0, 2): 2, (0, 3): 9, (0, 4): 4,
                      (1, 1): 6, (1, 2): 3, (1, 3): 9, (1, 4): 5}
        self.apo = 1800
        self.paired = {0: b"\x00\x00\x01x13\x00", 1: b"\x01\x01\x00Phone\x00"}

    # --- transport interface
    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False

    def _notify(self, feature, pdu, payload):
        if feature in self.registered and self.on_notification:
            self.on_notification(Packet(S, command_id(feature, pdu, PacketType.NOTIFICATION), payload))

    def _err(self, vendor, feature, pdu, code=GaiaError.INVALID_PARAMETER):
        raise GaiaCommandError(Packet(vendor, command_id(feature, pdu, PacketType.ERROR), bytes([code])))

    def request(self, vendor, feature, pdu, payload=b"", timeout=3.0):
        payload = bytes(payload)
        self.sent.append((vendor, feature, pdu, payload))
        if vendor == VENDOR_QUALCOMM:
            return {4: b"MOMENTUM TW 4", 3: b"00001234560000000000", 5: b"5.22.1"}[pdu]
        if feature == 0:
            if pdu == P.CORE_GET_SUPPORTED_FEATURES:
                return bytes.fromhex("0009010804 0e010c010302 1002 0404 0f01 1601 0201 0a03 1401 0b02 1501 0d01 0005".replace(" ", ""))
            if pdu == P.CORE_REGISTER_NOTIFICATION:
                self.registered.add(payload[0])
                return b""
            self._err(vendor, feature, pdu, GaiaError.COMMAND_NOT_SUPPORTED)
        key = (feature, pdu)
        # ---- setters
        if key == (0xC, 4):
            self.v[(0xC, 5)] = payload[:1]
            self._notify(0xC, 5, payload[:1])
            return b""
        if key == (0xD, 4):
            self.v[(0xD, 5)] = payload[:1]
            if payload[0]:
                self.v[(0xC, 5)] = b"\x00"      # ANC on clears transparency (observed)
            return b""
        if key == (0xC, 2):
            self.v[(0xC, 3)] = payload[:1]
            return b""
        if key == (0xD, 0):
            m = bytearray(self.v[(0xD, 1)])
            for i in range(0, 6, 2):
                if m[i] == payload[0]:
                    m[i + 1] = payload[1]
            self.v[(0xD, 1)] = bytes(m)
            return b""
        if key == (8, 1):
            if len(payload) != 2:
                self._err(vendor, feature, pdu)
            self.eq[payload[0]] = int.from_bytes(payload[1:2], "big", signed=True)
            self._notify(8, 2, bytes(g & 0xFF for g in self.eq))
            return b""
        if key == (8, 0):
            return bytes([5, 0xC4, 0x3C, 0, 0])
        if key == (8, 2):
            if not payload:
                self._err(vendor, feature, pdu)
            return bytes([self.eq[payload[0]] & 0xFF])
        if key == (0xB, 1):
            return bytes([payload[0], payload[1], self.touch[(payload[0], payload[1])]])
        if key == (0xB, 0):
            self.touch[(payload[0], payload[1])] = payload[2]
            return b""
        if key == (3, 1):
            if payload != b"\x00":
                self._err(vendor, feature, pdu)
            return b"\x00" + self.apo.to_bytes(2, "big")
        if key == (3, 0):
            self.apo = int.from_bytes(payload[1:3], "big")
            return b""
        if key == (0xA, 0):
            return b"\x00\x04"
        if key == (0xA, 1):
            if payload[0] not in self.paired:
                self._err(vendor, feature, pdu)
            return self.paired[payload[0]]
        if feature == 0x16 and pdu in (1, 2):
            self._notify(0x16, 3, bytes([1 if pdu == 2 else 0, payload[0]]))
            return b""
        # generic set pdus: map set -> get for simple 1-byte properties
        simple = {(8, 8): (8, 9), (4, 0x0C): (4, 0x0D), (2, 0): (2, 1), (4, 0x0A): (4, 0x0B),
                  (4, 0x14): (4, 0x15), (4, 0x17): (4, 0x18), (2, 5): (2, 6), (2, 7): (2, 8),
                  (4, 1): (4, 2), (4, 5): (4, 6), (4, 3): (4, 4)}
        if key in simple:
            self.v[simple[key]] = payload
            return b""
        if key in self.v:
            return self.v[key]
        self._err(vendor, feature, pdu, GaiaError.COMMAND_NOT_SUPPORTED)
