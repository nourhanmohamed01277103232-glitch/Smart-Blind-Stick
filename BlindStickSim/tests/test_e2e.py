"""اختبار شامل: نشغّل sim.py الحقيقي ونتأكد إنه بيتصل ويبعت فعلاً."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import phone  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Collector:
    def __init__(self, port):
        self.port = port
        self.msgs = []
        self.connected = False
        self._stop = False
        self.sock = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.port))
        self.sock.listen(1)
        self.sock.settimeout(0.2)
        self.thread.start()
        return self

    def _run(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.connected = True
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
                    try:
                        self.msgs.append(json.loads(line.decode()))
                    except ValueError:
                        pass
            try:
                conn.close()
            except OSError:
                pass

    def stop(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass


def wait_for(predicate, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class TestEndToEnd(unittest.TestCase):
    def test_sim_connects_and_streams(self):
        port = free_port()
        collector = Collector(port).start()
        env = dict(os.environ)
        env["SDL_VIDEODRIVER"] = "dummy"
        env["SDL_AUDIODRIVER"] = "dummy"
        env["PYTHONIOENCODING"] = "utf-8"
        env["STICK_PORT"] = str(port)
        proc = subprocess.Popen(
            [sys.executable, "sim.py"], cwd=BASE, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            self.assertTrue(wait_for(lambda: collector.connected), "السيميوليشن مفيش اتصل")
            self.assertTrue(wait_for(lambda: any(m.get("type") == "pos" for m in collector.msgs)),
                            "مفيش رسائل حركة")
            pos = next(m for m in collector.msgs if m.get("type") == "pos")
            self.assertIn("x", pos)
            self.assertIn("y", pos)
        finally:
            proc.terminate()
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, err = proc.communicate()
            collector.stop()
            text = (out or b"").decode("utf-8", "replace") + (err or b"").decode("utf-8", "replace")
            self.assertNotIn("Traceback", text, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
