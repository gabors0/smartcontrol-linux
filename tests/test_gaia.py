import unittest

from smartcontrol.gaia import (FrameDecoder, Packet, PacketType, VENDOR_QUALCOMM,
                               VENDOR_SENNHEISER, command_id)


class CommandIdTest(unittest.TestCase):
    def test_layout(self):
        self.assertEqual(command_id(0x0D, 0x01), 0x1A01)
        self.assertEqual(command_id(0x0D, 0x01, PacketType.RESPONSE), 0x1B01)
        self.assertEqual(command_id(0x0D, 0x05, PacketType.NOTIFICATION), 0x1A85)
        self.assertEqual(command_id(0x03, 0x03, PacketType.ERROR), 0x0783)


class EncodeTest(unittest.TestCase):
    def test_get_battery(self):
        self.assertEqual(Packet(VENDOR_SENNHEISER, 0x0603).encode(), bytes.fromhex("ff0300000495 0603"))

    def test_set_with_payload(self):
        self.assertEqual(Packet(VENDOR_SENNHEISER, 0x1A04, b"\x01").encode(),
                         bytes.fromhex("ff030001 0495 1a04 01"))

    def test_long_payload_uses_16bit_length(self):
        raw = Packet(VENDOR_SENNHEISER, 0x1000, bytes(300)).encode()
        self.assertEqual(raw[:5], bytes([0xFF, 0x03, 0x02, 0x01, 0x2C]))
        self.assertEqual(len(raw), 5 + 4 + 300)


class DecodeTest(unittest.TestCase):
    # Captured from a MOMENTUM True Wireless 4 (fw 5.22.1)
    API_VERSION = bytes.fromhex("ff030002001d01000301")
    BATTERY = bytes.fromhex("ff0300030495070346503c")

    def test_single_frame(self):
        (p,) = FrameDecoder().feed(self.API_VERSION)
        self.assertEqual(p.vendor, VENDOR_QUALCOMM)
        self.assertEqual(p.type, PacketType.RESPONSE)
        self.assertEqual((p.feature, p.pdu), (0, 0))
        self.assertEqual(p.payload, b"\x03\x01")

    def test_split_and_concatenated(self):
        d = FrameDecoder()
        data = self.API_VERSION + self.BATTERY
        self.assertEqual(d.feed(data[:3]), [])
        self.assertEqual(d.feed(data[3:13]), [Packet(VENDOR_QUALCOMM, 0x0100, b"\x03\x01")])
        (p,) = d.feed(data[13:])
        self.assertEqual(p.key, (VENDOR_SENNHEISER, 3, 3))
        self.assertEqual(p.payload, bytes([70, 80, 60]))

    def test_resync_on_garbage(self):
        (p,) = FrameDecoder().feed(b"\x00\x12" + self.BATTERY)
        self.assertEqual(p.payload, bytes([70, 80, 60]))

    def test_checksum(self):
        body = bytearray(self.BATTERY)
        body[2] = 0x01
        x = 0
        for b in body:
            x ^= b
        self.assertEqual(len(FrameDecoder().feed(bytes(body) + bytes([x]))), 1)
        self.assertEqual(FrameDecoder().feed(bytes(body) + bytes([x ^ 1])), [])


if __name__ == "__main__":
    unittest.main()
