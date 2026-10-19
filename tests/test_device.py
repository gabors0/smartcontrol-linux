import unittest

from smartcontrol import protocol as P
from smartcontrol.device import NoiseMode, SennheiserDevice
from smartcontrol.models import MTW4
from tests.fake_device import FakeMTW4


class DeviceTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMTW4()
        self.dev = SennheiserDevice("00:00:00:00:00:00", transport=self.fake)
        self.topics = []
        self.dev.add_listener(self.topics.append)
        self.dev.connect()

    def test_initial_state(self):
        st = self.dev.state
        self.assertIs(self.dev.profile, MTW4)
        self.assertEqual(st.variant, "MOMENTUM TW 4")
        self.assertEqual(st.firmware, "5.22.1")
        self.assertEqual(st.model_id, "MTW4 Graphite")
        self.assertEqual(st.battery, {"left": 70, "right": 80, "case": 60})
        self.assertEqual(st.noise_mode, NoiseMode.TRANSPARENCY)
        self.assertEqual(st.transparency_level, 50)
        self.assertTrue(st.anti_wind)
        self.assertEqual(st.eq_gains, [0.0] * 5)
        self.assertEqual(st.eq_range, (-6.0, 6.0))
        self.assertEqual(st.codec, P.Codec.AAC)
        self.assertEqual(st.sample_rate, 48000)
        self.assertEqual(st.touch[(P.Side.LEFT, P.TouchPattern.TAP)], P.TouchAction.TRANSPARENT_HEARING)
        self.assertEqual(st.settings["auto_power_off"], 30)
        self.assertEqual(st.settings["prompts"], P.PromptsMode.TONES)
        self.assertEqual(st.settings["sidetone"], 2)
        self.assertFalse(st.settings["podcast_mode"])
        self.assertEqual([d.name for d in st.paired], ["x13", "Phone"])
        self.assertIn("info", self.topics)

    def test_registers_only_supported_features(self):
        self.assertIn(P.Feature.ANC, self.fake.registered)
        self.assertNotIn(P.Feature.EARBUD, self.fake.registered)

    def test_noise_modes(self):
        self.dev.set_noise_mode("anc")
        self.assertEqual(self.dev.state.noise_mode, NoiseMode.ANC)
        self.dev.set_noise_mode(NoiseMode.OFF)
        self.assertEqual(self.dev.state.noise_mode, NoiseMode.OFF)
        self.assertFalse(self.dev.state.anc)
        self.dev.set_noise_mode("transparency")
        self.assertEqual(self.dev.state.noise_mode, NoiseMode.TRANSPARENCY)

    def test_anc_mode_turns_transparency_off_first(self):
        self.fake.sent.clear()
        self.dev.set_noise_mode("anc")
        sets = [(f, p, pl) for _, f, p, pl in self.fake.sent if p in (4,) and f in (0xC, 0xD)]
        self.assertEqual(sets, [(0xC, 4, b"\x00"), (0xD, 4, b"\x01")])

    def test_anti_wind_and_transparency_level(self):
        self.dev.set_anti_wind(False)
        self.assertFalse(self.dev.state.anti_wind)
        self.assertEqual(self.fake.v[(0xD, 1)], bytes([1, 0, 2, 0, 3, 0]))
        self.dev.set_transparency_level(150)
        self.assertEqual(self.dev.state.transparency_level, 100)

    def test_eq(self):
        self.dev.set_eq_band(0, 1.0)
        self.assertEqual(self.fake.eq[0], 10)
        self.assertEqual(self.dev.state.eq_gains[0], 1.0)
        self.dev.set_eq(MTW4.eq_presets["Rock"])
        self.assertEqual(self.fake.eq, [-25, 5, 25, -5, -35])
        self.assertEqual(self.dev.state.eq_gains, [-2.5, 0.5, 2.5, -0.5, -3.5])
        self.dev.set_eq_band(4, -20)
        self.assertEqual(self.fake.eq[4], -60)       # clamped to device range

    def test_touch(self):
        self.dev.set_touch_action(P.Side.RIGHT, P.TouchPattern.TAP, P.TouchAction.VOICE_ASSISTANT)
        self.assertEqual(self.fake.touch[(0, 1)], 7)
        self.assertEqual(self.dev.state.touch[(P.Side.RIGHT, P.TouchPattern.TAP)], P.TouchAction.VOICE_ASSISTANT)
        with self.assertRaises(ValueError):
            self.dev.set_touch_action(P.Side.RIGHT, P.TouchPattern.TAP, P.TouchAction.VOLUME_UP)

    def test_settings(self):
        self.dev.set_setting("gaming_mode", False)
        self.assertFalse(self.dev.state.settings["gaming_mode"])
        self.dev.set_setting("auto_power_off", 60)
        self.assertEqual(self.fake.apo, 3600)
        self.assertEqual(self.dev.state.settings["auto_power_off"], 60)
        self.dev.set_setting("prompts", P.PromptsMode.OFF)
        self.assertEqual(self.dev.state.settings["prompts"], P.PromptsMode.OFF)
        self.dev.set_setting("podcast_mode", True)
        self.assertEqual(self.fake.v[(4, 4)], bytes([0, 2]))
        self.assertTrue(self.dev.state.settings["podcast_mode"])
        with self.assertRaises(ValueError):
            self.dev.set_setting("sidetone", 9)

    def test_notifications_update_state(self):
        self.topics.clear()
        self.fake._notify(3, 3, bytes([50, 51, 52]))
        self.assertEqual(self.dev.state.battery["case"], 52)
        self.assertEqual(self.topics, ["battery"])

    def test_find_state(self):
        self.dev.find_start(P.Side.RIGHT)
        self.assertTrue(self.dev.state.ringing[P.Side.RIGHT])
        self.dev.find_stop(P.Side.RIGHT)
        self.assertFalse(self.dev.state.ringing[P.Side.RIGHT])


if __name__ == "__main__":
    unittest.main()
