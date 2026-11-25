"""اختبار منطق الواجهة من غير ما نفتح نافذة Tk حقيقية."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import phone  # noqa: E402


class FakeLabel:
    def __init__(self):
        self.text = None
        self.fg = None

    def config(self, **kw):
        if "text" in kw:
            self.text = kw["text"]
        if "fg" in kw:
            self.fg = kw["fg"]


class FakeSpeaker:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)


def make_phone():
    p = phone.Phone.__new__(phone.Phone)
    p.root = None
    p.alert = FakeLabel()
    p.status = FakeLabel()
    p.gps = FakeLabel()
    p.speaker = FakeSpeaker()
    p.last_rx = time.monotonic()
    p.connected = False
    p.lat, p.lon = phone.BASE_LAT, phone.BASE_LON
    p.fall_started = False
    p.start_fall = lambda: setattr(p, "fall_started", True)
    return p


class TestHandle(unittest.TestCase):
    def test_alert_shows_arabic_and_speaks(self):
        p = make_phone()
        p.handle({"type": "alert", "name": "Car", "dist": 120})
        self.assertIn("سيارة", p.alert.text)
        self.assertIn("120", p.alert.text)
        self.assertEqual(p.speaker.said, [p.alert.text])

    def test_alert_clear(self):
        p = make_phone()
        p.handle({"type": "alert", "name": "Pit", "dist": 40})
        p.handle({"type": "alert", "name": None, "dist": None})
        self.assertEqual(p.alert.text, "No alerts")

    def test_pos_updates_gps(self):
        p = make_phone()
        p.handle({"type": "pos", "x": 300, "y": 200})
        self.assertNotEqual(p.lat, phone.BASE_LAT)
        self.assertIn("GPS:", p.gps.text)

    def test_fall_starts_countdown(self):
        p = make_phone()
        p.handle({"type": "fall"})
        self.assertTrue(p.fall_started)

    def test_malformed_messages_do_not_crash(self):
        p = make_phone()
        for msg in ({"type": "alert"}, {"type": "pos"}, {"type": "???"}, {}):
            p.handle(msg)

    def test_error_event_shows_port_problem(self):
        p = make_phone()
        p.handle({"type": "error", "message": "address in use"})
        self.assertIn("Port busy", p.status.text)

    def test_any_message_marks_connected(self):
        p = make_phone()
        p.handle({"type": "pos", "x": 80, "y": 500})
        self.assertTrue(p.connected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
