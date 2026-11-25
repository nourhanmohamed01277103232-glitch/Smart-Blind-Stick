"""اختبارات قناة الاتصال بين السيميوليشن والموبايل."""

import json
import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stick_link import StickLink  # noqa: E402


class FakePhone:
    """سيرفر وهمي بياخد مكان phone.py، بيسجل كل رسالة وصلته."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(2)
        self.sock.settimeout(0.2)
        self.lines = []
        self.conn = None
        self.connections = 0
        self._stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.connections += 1
            self.conn = conn
            conn.settimeout(0.2)
            buf = b""
            while not self._stop:
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self.lines.append(json.loads(line.decode()))
            try:
                conn.close()
            except OSError:
                pass

    def drop_client(self):
        if self.conn:
            try:
                self.conn.close()
            except OSError:
                pass

    def stop(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass


def wait_for(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestStickLink(unittest.TestCase):
    def setUp(self):
        self.phone = FakePhone()
        self.link = StickLink("127.0.0.1", self.phone.port).start()

    def tearDown(self):
        self.link.stop()
        self.phone.stop()

    def test_connects_and_delivers(self):
        self.assertTrue(wait_for(lambda: self.link.connected), "لازم يتصل")
        self.link.send({"type": "pos", "x": 10.0, "y": 20.0})
        self.link.send({"type": "alert", "name": "Car", "dist": 120})
        self.assertTrue(wait_for(lambda: len(self.phone.lines) >= 2))
        self.assertEqual(self.phone.lines[0], {"type": "pos", "x": 10.0, "y": 20.0})
        self.assertEqual(self.phone.lines[1]["name"], "Car")

    def test_arabic_payload_is_utf8(self):
        self.assertTrue(wait_for(lambda: self.link.connected))
        self.link.send({"type": "alert", "name": "سيارة"})
        self.assertTrue(wait_for(lambda: len(self.phone.lines) >= 1))
        self.assertEqual(self.phone.lines[0]["name"], "سيارة")

    def test_reconnects_after_drop(self):
        self.assertTrue(wait_for(lambda: self.link.connected))
        self.phone.drop_client()
        # لازم يرجع يتصل لوحده من غير تدخل
        self.assertTrue(wait_for(lambda: self.phone.connections >= 2, 6.0), "لازم يعيد الاتصال")
        self.link.send({"type": "pos", "x": 1, "y": 2})
        self.assertTrue(wait_for(lambda: len(self.phone.lines) >= 1))

    def test_send_never_blocks_when_offline(self):
        dead = StickLink("127.0.0.1", 1, retry=0.05).start()
        try:
            start = time.time()
            for _ in range(200):
                dead.send({"type": "pos", "x": 0, "y": 0})
            elapsed = time.time() - start
            self.assertLess(elapsed, 0.5, "الإرسال مينفعش يعمل blocking")
            self.assertFalse(dead.connected)
        finally:
            dead.stop()

    def test_startup_does_not_wait_for_phone(self):
        """السيميوليشن لازم يشتغل حتى لو الموبايل مقفول."""
        start = time.time()
        link = StickLink("127.0.0.1", 1, retry=0.05).start()
        elapsed = time.time() - start
        link.stop()
        self.assertLess(elapsed, 0.2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
