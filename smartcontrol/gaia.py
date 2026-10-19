"""Qualcomm GAIA v3 framing as spoken over RFCOMM by Sennheiser headphones.

Wire format (observed on MOMENTUM True Wireless 4, fw 5.22.1)::

    0xFF | version | flags | length(1 or 2) | vendor(2) | command(2) | payload | [checksum]

* version is 0x03 for GAIA v3 (the device answers with 3 regardless of what we send)
* flags bit0: trailing XOR checksum present, bit1: 16-bit length field
* command = feature(7 bits) << 9 | type(2 bits) << 7 | pdu(7 bits)
  type: 0 command, 1 notification, 2 response, 3 error
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

SOF = 0xFF
VERSION = 0x03
FLAG_CHECKSUM = 0x01
FLAG_LONG_LENGTH = 0x02

VENDOR_QUALCOMM = 0x001D
VENDOR_SENNHEISER = 0x0495


class PacketType(IntEnum):
    COMMAND = 0
    NOTIFICATION = 1
    RESPONSE = 2
    ERROR = 3


class GaiaError(IntEnum):
    """Status codes carried in the first payload byte of an ERROR packet."""
    FEATURE_NOT_SUPPORTED = 0
    COMMAND_NOT_SUPPORTED = 1
    NOT_AUTHENTICATED = 2
    INSUFFICIENT_RESOURCES = 3
    AUTHENTICATING = 4
    INVALID_PARAMETER = 5
    INCORRECT_STATE = 6
    IN_PROGRESS = 7


def command_id(feature: int, pdu: int, ptype: PacketType = PacketType.COMMAND) -> int:
    return ((feature & 0x7F) << 9) | ((int(ptype) & 0x3) << 7) | (pdu & 0x7F)


@dataclass(frozen=True)
class Packet:
    vendor: int
    command: int
    payload: bytes = b""

    @property
    def feature(self) -> int:
        return self.command >> 9

    @property
    def type(self) -> PacketType:
        return PacketType((self.command >> 7) & 0x3)

    @property
    def pdu(self) -> int:
        return self.command & 0x7F

    @property
    def key(self) -> tuple[int, int, int]:
        """(vendor, feature, pdu) – identifies the request a response belongs to."""
        return self.vendor, self.feature, self.pdu

    def encode(self) -> bytes:
        n = len(self.payload)
        if n > 0xFFFF:
            raise ValueError("payload too large")
        if n > 0xFF:
            head = bytes([SOF, VERSION, FLAG_LONG_LENGTH]) + n.to_bytes(2, "big")
        else:
            head = bytes([SOF, VERSION, 0, n])
        return head + self.vendor.to_bytes(2, "big") + self.command.to_bytes(2, "big") + self.payload

    def __str__(self) -> str:
        return (f"<{self.type.name} v={self.vendor:04x} feat={self.feature:#04x} "
                f"pdu={self.pdu:#04x} payload={self.payload.hex(' ')}>")


class FrameDecoder:
    """Incremental decoder: feed raw bytes, collect complete packets."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Packet]:
        self._buf += data
        out: list[Packet] = []
        while True:
            # resynchronise on start-of-frame
            start = self._buf.find(bytes([SOF]))
            if start < 0:
                self._buf.clear()
                return out
            if start:
                del self._buf[:start]
            if len(self._buf) < 4:
                return out
            flags = self._buf[2]
            if flags & FLAG_LONG_LENGTH:
                if len(self._buf) < 5:
                    return out
                length, header = int.from_bytes(self._buf[3:5], "big"), 5
            else:
                length, header = self._buf[3], 4
            total = header + 4 + length + (1 if flags & FLAG_CHECKSUM else 0)
            if len(self._buf) < total:
                return out
            frame = bytes(self._buf[:total])
            del self._buf[:total]
            if flags & FLAG_CHECKSUM:
                x = 0
                for b in frame[:-1]:
                    x ^= b
                if x != frame[-1]:
                    continue
            vendor = int.from_bytes(frame[header:header + 2], "big")
            command = int.from_bytes(frame[header + 2:header + 4], "big")
            out.append(Packet(vendor, command, frame[header + 4:header + 4 + length]))
