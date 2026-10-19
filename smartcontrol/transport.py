"""RFCOMM transport: one socket, a reader thread, request/response matching."""
from __future__ import annotations

import logging
import socket
import threading
from typing import Callable

from . import sdp
from .gaia import FrameDecoder, GaiaError, Packet, PacketType, command_id

log = logging.getLogger(__name__)


class GaiaCommandError(Exception):
    def __init__(self, packet: Packet):
        self.packet = packet
        code = packet.payload[0] if packet.payload else -1
        try:
            self.code = GaiaError(code)
            name = self.code.name
        except ValueError:
            self.code, name = code, f"error {code}"
        super().__init__(f"{name} for v={packet.vendor:04x} feat={packet.feature:#04x} pdu={packet.pdu:#04x}")


class _Pending:
    __slots__ = ("event", "packet")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.packet: Packet | None = None


class GaiaTransport:
    def __init__(self, address: str, channel: int | None = None):
        self.address = address
        self.channel = channel
        self._sock: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._pending: dict[tuple[int, int, int], list[_Pending]] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self.on_notification: Callable[[Packet], None] | None = None
        self.on_disconnect: Callable[[Exception | None], None] | None = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self, timeout: float = 10.0) -> None:
        if self.channel is None:
            self.channel = sdp.find_gaia_channel(self.address)
            if self.channel is None:
                raise ConnectionError("device has no Sennheiser GAIA RFCOMM service")
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        s.settimeout(timeout)
        s.connect((self.address, self.channel))
        s.settimeout(None)
        self._sock = s
        self._reader = threading.Thread(target=self._read_loop, name="gaia-reader", daemon=True)
        self._reader.start()
        log.info("connected to %s on RFCOMM channel %d", self.address, self.channel)

    def close(self) -> None:
        s, self._sock = self._sock, None
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()

    def _read_loop(self) -> None:
        dec = FrameDecoder()
        err: Exception | None = None
        try:
            while self._sock:
                data = self._sock.recv(4096)
                if not data:
                    break
                for p in dec.feed(data):
                    log.debug("<< %s", p)
                    self._dispatch(p)
        except OSError as e:
            err = e if self._sock else None
        finally:
            was_open = self._sock is not None
            self._sock = None
            with self._lock:
                waiters = [w for ws in self._pending.values() for w in ws]
                self._pending.clear()
            for w in waiters:
                w.event.set()
            if was_open and self.on_disconnect:
                self.on_disconnect(err)

    def _dispatch(self, p: Packet) -> None:
        if p.type in (PacketType.RESPONSE, PacketType.ERROR):
            with self._lock:
                q = self._pending.get(p.key)
                w = q.pop(0) if q else None
            if w:
                w.packet = p
                w.event.set()
            return
        if p.type == PacketType.NOTIFICATION and self.on_notification:
            try:
                self.on_notification(p)
            except Exception:
                log.exception("notification handler failed")

    def request(self, vendor: int, feature: int, pdu: int, payload: bytes = b"",
                timeout: float = 3.0) -> bytes:
        """Send a command and return the response payload (raises GaiaCommandError)."""
        if not self._sock:
            raise ConnectionError("not connected")
        pkt = Packet(vendor, command_id(feature, pdu), bytes(payload))
        w = _Pending()
        with self._lock:
            self._pending.setdefault(pkt.key, []).append(w)
        log.debug(">> %s", pkt)
        try:
            with self._send_lock:
                self._sock.sendall(pkt.encode())
        except OSError:
            with self._lock:
                self._pending.get(pkt.key, []).remove(w)
            raise
        if not w.event.wait(timeout):
            with self._lock:
                q = self._pending.get(pkt.key, [])
                if w in q:
                    q.remove(w)
            raise TimeoutError(f"no response to {pkt}")
        if w.packet is None:
            raise ConnectionError("disconnected")
        if w.packet.type == PacketType.ERROR:
            raise GaiaCommandError(w.packet)
        return w.packet.payload
