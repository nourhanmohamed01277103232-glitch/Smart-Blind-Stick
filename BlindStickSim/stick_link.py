"""قناة الاتصال بين العصاية (sim.py) وتطبيق الموبايل (phone.py).

المشكلة القديمة: sim.py كان بيحاول يتصل مرة واحدة بس عند التشغيل.
لو الموبايل فتح بعد السيميوليشن، أو الاتصال قطع، مفيش إعادة محاولة خالص.

الحل: Thread في الخلفية بيعيد المحاولة كل ثانية، واللعبة بتحط الرسائل
في Queue فمفيش أي blocking على الـ main loop.
"""

import json
import queue
import select
import socket
import threading
import time


def _log(msg):
    """print آمن: لو الترمنال مش بيقبل العربي (cp1252) ميكسرش الـ thread."""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass


class StickLink:
    def __init__(self, host="127.0.0.1", port=5005, retry=1.0):
        self.host = host
        self.port = port
        self.retry = retry
        self.connected = False
        self._sock = None
        self._stop = False
        self._q = queue.Queue(maxsize=64)
        self._thread = threading.Thread(target=self._run, name="stick-link", daemon=True)

    # ---------- API ----------
    def start(self):
        self._thread.start()
        return self

    def send(self, msg):
        """آمنة للنداء من أي thread، ومبتعملش blocking لو الاتصال واقع."""
        try:
            self._q.put_nowait(msg)
        except queue.Full:
            # الأهم إن آخر رسالة توصل، فالقديمة بتتشال
            try:
                self._q.get_nowait()
                self._q.put_nowait(msg)
            except queue.Empty:
                pass

    def stop(self):
        self._stop = True
        self._close()

    # ---------- داخلي ----------
    def _connect(self):
        s = socket.create_connection((self.host, self.port), timeout=1.0)
        s.settimeout(1.0)
        return s

    def _close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self.connected = False

    def _peer_gone(self):
        """بيكتشف لو الموبايل قفل الاتصال من غير ما نبعت حاجة."""
        if self._sock is None:
            return True
        try:
            r, _, _ = select.select([self._sock], [], [], 0)
            if not r:
                return False
            data = self._sock.recv(1)
            return data == b""
        except OSError:
            return True

    def _run(self):
        while not self._stop:
            if self._sock is None:
                try:
                    self._sock = self._connect()
                    self.connected = True
                    _log(f"[link] متصل بتطبيق الموبايل على {self.host}:{self.port}")
                except OSError:
                    self.connected = False
                    time.sleep(self.retry)
                    continue
            try:
                msg = self._q.get(timeout=0.2)
            except queue.Empty:
                if self._peer_gone():
                    _log("[link] الاتصال قطع، جاري إعادة المحاولة...")
                    self._close()
                continue
            try:
                self._sock.sendall((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
            except OSError:
                _log("[link] الاتصال قطع، جاري إعادة المحاولة...")
                self._close()
