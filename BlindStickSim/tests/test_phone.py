"""اختبارات تطبيق الموبايل (السيرفر + شكل التنبيه + الإعدادات)."""

import json
import os
import queue
import socket
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import phone  # noqa: E402


def wait_for(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestAlertText(unittest.TestCase):
    def test_live_distance(self):
        self.assertEqual(phone.format_alert("Car", 120),
                         "احذر! يوجد سيارة على بعد 120 سنتيمتر")

    def test_same_text_as_simulation(self):
        """نفس الجملة اللي السيميوليشن بيكتبها بالحرف."""
        import stick_audio
        for name, d in [("Car", 120), ("Pit", 0), ("Water", 160)]:
            self.assertEqual(phone.format_alert(name, d), stick_audio.warn_raw(name, d, "ar"))

    def test_zero_distance(self):
        self.assertEqual(phone.format_alert("Pit", 0), "احذر! يوجد حفرة أمامك مباشرة")

    def test_none_distance(self):
        self.assertEqual(phone.format_alert("Water", None), "احذر! يوجد مياه أمامك مباشرة")

    def test_unknown_type_falls_back_to_english(self):
        self.assertIn("Dog", phone.format_alert("Dog", 50))


class TestConfig(unittest.TestCase):
    def test_defaults_are_sane(self):
        cfg = phone.load_config()
        self.assertIsInstance(cfg["port"], int)
        self.assertTrue(0 < cfg["port"] < 65536)
        self.assertGreaterEqual(cfg["fall_seconds"], 1)
        self.assertIsInstance(cfg["telegram"]["chat_ids"], list)

    def test_env_override(self):
        os.environ["STICK_BOT_TOKEN"] = "123:ABC"
        os.environ["STICK_CHAT_IDS"] = "1, 2"
        try:
            cfg = phone.load_config()
            self.assertEqual(cfg["telegram"]["token"], "123:ABC")
            self.assertEqual(cfg["telegram"]["chat_ids"], ["1", "2"])
        finally:
            del os.environ["STICK_BOT_TOKEN"]
            del os.environ["STICK_CHAT_IDS"]


class TestServer(unittest.TestCase):
    def setUp(self):
        self.events = queue.Queue()
        self.server = phone.StickServer("127.0.0.1", 0, self.events)
        self.server.start()
        assert wait_for(lambda: self.server._lsock is not None), "السيرفر مش فتح"
        self.port = self.server.port
        self.client = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        assert wait_for(lambda: self._drain_has("connected")), "مفيش حدث اتصال"

    def tearDown(self):
        try:
            self.client.close()
        except OSError:
            pass
        self.server.stop()

    def _drain(self):
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out

    def _drain_has(self, t):
        got = self._drain()
        for g in got:
            if g.get("type") == t:
                return True
        return False

    def test_pos_and_alert(self):
        self.client.sendall(json.dumps({"type": "pos", "x": 300, "y": 200}).encode() + b"\n")
        self.client.sendall(json.dumps({"type": "alert", "name": "Car", "dist": 80}).encode() + b"\n")
        self.assertTrue(wait_for(lambda: self.events.qsize() >= 2))
        msgs = self._drain()
        self.assertEqual(msgs[0]["type"], "pos")
        self.assertEqual(msgs[1]["dist"], 80)

    def test_partial_message_is_buffered(self):
        """رسالة جاية على قطعتين لازم تتقرأ صح (كانت بتضيع)."""
        payload = json.dumps({"type": "alert", "name": "Water", "dist": 30}).encode()
        self.client.sendall(payload[:10])
        time.sleep(0.1)
        self.client.sendall(payload[10:] + b"\n")
        self.assertTrue(wait_for(lambda: self.events.qsize() >= 1))
        msg = self._drain()[0]
        self.assertEqual(msg["name"], "Water")

    def test_fall_event(self):
        self.client.sendall(b'{"type": "fall"}\n')
        self.assertTrue(wait_for(lambda: self.events.qsize() >= 1))
        self.assertEqual(self._drain()[0]["type"], "fall")

    def test_disconnect_event(self):
        self.client.close()
        self.assertTrue(wait_for(lambda: self._drain_has("disconnected"), 6.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
