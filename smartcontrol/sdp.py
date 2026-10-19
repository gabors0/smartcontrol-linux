"""Tiny SDP client (L2CAP PSM 1) used to find the GAIA RFCOMM channel.

We only need ServiceSearchAttribute over "all L2CAP services" and the
ServiceClassIDList / ProtocolDescriptorList / ServiceName attributes.
"""
from __future__ import annotations

import socket
import struct
from dataclasses import dataclass, field

# Sennheiser's GAIA service class. Also used as GATT service UUID on BLE models.
SENNHEISER_GAIA_UUID = "a2129ff3-081b-4c45-8afe-469d9c4842ec"
# Apple iAP2 – sending GAIA frames there powers some Sennheiser headsets off. Never use it.
IAP2_UUID = "00000000-deca-fade-deca-deafdecacaff"


@dataclass
class ServiceRecord:
    name: str | None
    uuids: list[str] = field(default_factory=list)
    rfcomm_channel: int | None = None


def _parse_element(b: bytes, i: int = 0):
    d = b[i]
    t, sz = d >> 3, d & 7
    i += 1
    if t == 0:
        return None, i
    if sz < 5:
        n = (1, 2, 4, 8, 16)[sz]
    else:
        ln = {5: 1, 6: 2, 7: 4}[sz]
        n = int.from_bytes(b[i:i + ln], "big")
        i += ln
    data = b[i:i + n]
    i += n
    if t in (1, 2):
        return int.from_bytes(data, "big"), i
    if t == 3:
        if n == 2:
            return "%04x" % int.from_bytes(data, "big"), i
        if n == 4:
            return "%08x" % int.from_bytes(data, "big"), i
        h = data.hex()
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}", i
    if t in (4, 8):
        return data.rstrip(b"\0").decode(errors="replace"), i
    if t == 5:
        return bool(data[0]), i
    if t in (6, 7):
        out, j = [], 0
        while j < len(data):
            v, j = _parse_element(data, j)
            out.append(v)
        return out, i
    return data, i


def parse_records(blob: bytes) -> list[ServiceRecord]:
    records, _ = _parse_element(blob)
    out = []
    for rec in records or []:
        attrs = {rec[k]: rec[k + 1] for k in range(0, len(rec) - 1, 2)}
        uuids = attrs.get(0x0001, [])
        if not isinstance(uuids, list):
            uuids = [uuids]
        chan = None
        for proto in attrs.get(0x0004, []) or []:
            if isinstance(proto, list) and proto and proto[0] == "0003" and len(proto) > 1:
                chan = proto[1]
        name = attrs.get(0x0100)
        out.append(ServiceRecord(name if isinstance(name, str) else None, uuids, chan))
    return out


def browse(address: str, timeout: float = 10.0) -> list[ServiceRecord]:
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    s.settimeout(timeout)
    try:
        s.connect((address, 1))
        pattern = bytes([0x35, 3, 0x19, 0x01, 0x00])               # UUID16 L2CAP
        attr_range = bytes([0x35, 5, 0x0A, 0x00, 0x00, 0xFF, 0xFF])  # all attributes
        cont, blob, tid = b"\x00", b"", 1
        while True:
            params = pattern + struct.pack(">H", 0xFFFF) + attr_range + cont
            s.send(struct.pack(">BHH", 0x06, tid, len(params)) + params)
            r = s.recv(4096)
            if r[0] != 0x07:
                raise OSError(f"SDP error response {r.hex()}")
            count = struct.unpack(">H", r[5:7])[0]
            blob += r[7:7 + count]
            cont = r[7 + count:]
            tid += 1
            if not cont or cont[0] == 0:
                break
    finally:
        s.close()
    return parse_records(blob)


def find_gaia_channel(address: str) -> int | None:
    for rec in browse(address):
        if SENNHEISER_GAIA_UUID in rec.uuids and rec.rfcomm_channel:
            return rec.rfcomm_channel
    return None
