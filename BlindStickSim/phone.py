"""تطبيق الموبايل: بيستقبل بيانات الحركة والتنبيهات من العصاية (sim.py).

تشغيله:  python phone.py   ثم  python sim.py

أهم التعديلات:
- السيرفر بيعيد المحاولة لو البورت مشغول، ومش بيموت بصمت.
- الاتصال بيتعافى تلقائياً (لو الموبايل فتح بعد السيميوليشن).
- التنبيه ممكن يوصل قبل الاتصال أو بعده من غير ما يعلّق الواجهة.
- صوت احتياطي: pyttsx3 ثم edge-tts+pygame، فالصوت مش بيسكت لو مكتبة ناقصة.
- توكن البوت بقى في config.json مش جوه الكود.
"""

import json
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request

# ---------- المسار ----------
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 5005,
    "fall_seconds": 30,
    "college": {"lat": 30.0562001, "lon": 31.3177229},
    "telegram": {"token": "", "chat_ids": []},
    "voice": {"pc": True, "phone": False},
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))  # نسخة عميقة
    try:
        with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
            user = json.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except (OSError, ValueError):
        pass
    # متغيرات البيئة بتاخد الأولوية
    tok = os.environ.get("STICK_BOT_TOKEN")
    if tok:
        cfg["telegram"]["token"] = tok
    ids = os.environ.get("STICK_CHAT_IDS")
    if ids:
        cfg["telegram"]["chat_ids"] = [x.strip() for x in ids.split(",") if x.strip()]
    return cfg


CONFIG = load_config()
HOST = CONFIG["host"]
PORT = int(os.environ.get("STICK_PORT", CONFIG["port"]))
FALL_SECONDS = int(CONFIG["fall_seconds"])
BOT_TOKEN = CONFIG["telegram"]["token"]
CHAT_IDS = CONFIG["telegram"]["chat_ids"]
BASE_LAT = float(CONFIG["college"]["lat"])
BASE_LON = float(CONFIG["college"]["lon"])
PHONE_SPEAK = CONFIG.get("voice", {}).get("phone", False)

BG = "#1e1e1e"

NAMES_AR = {"Car": "سيارة", "Person": "شخص", "Stairs": "سلم",
            "Chair": "كرسي", "Water": "مياه", "Pit": "حفرة"}


def format_alert(name, d):
    """نفس نص السيميوليشن بالحرف، عشان الرقم يبان متطابق في الاتنين."""
    n = NAMES_AR.get(name, name)
    if d is None or d <= 0:
        return f"احذر! يوجد {n} أمامك مباشرة"
    return f"احذر! يوجد {n} على بعد {int(d)} سنتيمتر"

# الأحداث الجاية من الشبكة (السيرفر بيكتب، الواجهة بتقرأ)
q = queue.Queue()


def _log(*args):
    """print آمن: الترمنال العربي (cp1252) ميكسرش أي thread."""
    try:
        sys.stdout.write(" ".join(str(a) for a in args) + "\n")
        sys.stdout.flush()
    except UnicodeEncodeError:
        sys.stdout.write(" ".join(str(a).encode("ascii", "replace").decode() for a in args) + "\n")
        sys.stdout.flush()


# ================== الصوت ==================
class Speaker:
    """طابور صوت واحد، عشان الأصوات متتراكبش، ومش بيعطّل الواجهة."""

    def __init__(self):
        self.q = queue.Queue()
        self._last_text = ""
        self._last_time = 0.0
        self._cache = {}
        self._dir = os.path.join(BASE_DIR, "voice_cache_phone")
        os.makedirs(self._dir, exist_ok=True)
        self._mixer_ready = False
        threading.Thread(target=self._worker, daemon=True).start()

    def say(self, text):
        now = time.time()
        if text == self._last_text and now - self._last_time < 1.0:
            return
        self._last_text, self._last_time = text, now
        self.q.put(text)

    # ---------- التشغيل ----------
    def _worker(self):
        while True:
            text = self.q.get()
            try:
                if not self._pyttsx3(text):
                    if not self._edge(text):
                        _log("[voice]", text)
            except Exception as e:  # noqa: BLE001
                _log("Voice error:", e)

    def _pyttsx3(self, text):
        try:
            import pyttsx3  # noqa: PLC0415
        except ImportError:
            return False
        try:
            engine = pyttsx3.init()
            for v in engine.getProperty("voices"):
                if "arab" in (v.name + v.id).lower():
                    engine.setProperty("voice", v.id)
                    break
            engine.setProperty("rate", 160)
            engine.say(text)
            engine.runAndWait()
            return True
        except Exception as e:  # noqa: BLE001
            _log("pyttsx3 error:", e)
            return False

    def _edge(self, text):
        try:
            import asyncio  # noqa: PLC0415

            import edge_tts  # noqa: PLC0415
            import pygame  # noqa: PLC0415
        except ImportError:
            return False
        try:
            if text not in self._cache:
                import hashlib  # noqa: PLC0415

                name = hashlib.md5(text.encode("utf-8")).hexdigest() + ".mp3"
                path = os.path.join(self._dir, name)
                if not os.path.exists(path):
                    asyncio.run(edge_tts.Communicate(text, "ar-EG-SalmaNeural").save(path))
                self._cache[text] = path
            if not self._mixer_ready:
                pygame.mixer.init()
                self._mixer_ready = True
            pygame.mixer.Sound(self._cache[text]).play()
            return True
        except Exception as e:  # noqa: BLE001
            _log("edge-tts error:", e)
            return False


# ================== تليجرام ==================
_send_q = queue.Queue()


def _telegram_worker():
    while True:
        text = _send_q.get()
        if not BOT_TOKEN or not CHAT_IDS:
            _log("[telegram] مفيش توكن أو chat ids في config.json")
            continue
        for cid in CHAT_IDS:
            try:
                data = urllib.parse.urlencode({"chat_id": cid, "text": text}).encode()
                with urllib.request.urlopen(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data, timeout=10
                ):
                    pass
            except Exception as e:  # noqa: BLE001
                _log("Telegram error:", e)


def telegram_send(text):
    _send_q.put(text)


# ================== السيرفر ==================
class StickServer(threading.Thread):
    """بيستقبل اتصال العصاية، وبيعيد المحاولة لو البورت مشغول."""

    def __init__(self, host, port, events, on_status=None):
        super().__init__(daemon=True, name="stick-server")
        self.host = host
        self.port = port
        self.events = events
        self.on_status = on_status
        self._stop = threading.Event()
        self._lsock = None
        self._conn = None

    def run(self):
        while not self._stop.is_set():
            if self._lsock is None:
                if not self._listen():
                    self._stop.wait(1.0)
                    continue
            try:
                self._lsock.settimeout(0.5)
                conn, _ = self._lsock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                self._lsock = None
                continue
            self._conn = conn
            self.events.put({"type": "connected"})
            self._handle(conn)

    def _listen(self):
        try:
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen(1)
            self._lsock = s
            self.port = s.getsockname()[1]
            return True
        except OSError as e:
            self.events.put({"type": "error", "message": f"مش قادر يفتح البورت {self.port}: {e}"})
            return False

    def _handle(self, conn):
        buf = b""
        conn.settimeout(0.5)
        try:
            while not self._stop.is_set():
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
                    if not line.strip():
                        continue
                    try:
                        self.events.put(json.loads(line.decode("utf-8", "replace")))
                    except ValueError:
                        _log("[server] رسالة غير مفهومة:", line[:80])
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self._conn = None
            self.events.put({"type": "disconnected"})

    def stop(self):
        self._stop.set()
        for s in (self._conn, self._lsock):
            try:
                if s:
                    s.close()
            except OSError:
                pass


# ================== الواجهة ==================
class Phone:
    def __init__(self, root, speaker, server):
        self.root = root
        self.speaker = speaker
        self.server = server
        self.last_rx = time.monotonic()
        self.connected = False

        root.title("Phone App")
        root.geometry("380x720")
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.lat, self.lon = BASE_LAT, BASE_LON
        self.fall_left = None
        self.contacts = ["Mama - 01000000001", "Friend - 01000000002"]

        self.status = tk.Label(root, text="Stick: not connected", bg=BG, fg="orange",
                               font=("Segoe UI", 12, "bold"))
        self.status.pack(pady=(10, 2))
        self.gps = tk.Label(root, text="", bg=BG, fg="white", font=("Segoe UI", 11))
        self.gps.pack()
        self.alert = tk.Label(root, text="No alerts", bg=BG, fg="lightgreen",
                              font=("Segoe UI", 14, "bold"), wraplength=340)
        self.alert.pack(pady=10)

        tk.Button(root, text="SOS", bg="red", fg="white", font=("Segoe UI", 22, "bold"),
                  command=lambda: self.send_sms("SOS button pressed")).pack(fill="x", padx=20, pady=5)

        self.fall_frame = tk.Frame(root, bg=BG)
        self.fall_frame.pack(fill="x", padx=20)
        self.fall_label = tk.Label(self.fall_frame, bg=BG, fg="yellow", font=("Segoe UI", 13, "bold"))
        self.ok_btn = tk.Button(self.fall_frame, text="I'm OK", bg="green", fg="white",
                                font=("Segoe UI", 16, "bold"), command=self.cancel_fall)

        tk.Label(root, text="Emergency Contacts", bg=BG, fg="white",
                 font=("Segoe UI", 12, "bold")).pack(pady=(12, 2))
        self.listbox = tk.Listbox(root, height=4)
        self.listbox.pack(fill="x", padx=20)
        for c in self.contacts:
            self.listbox.insert("end", c)
        row = tk.Frame(root, bg=BG)
        row.pack(fill="x", padx=20, pady=4)
        self.entry = tk.Entry(row)
        self.entry.pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Add", command=self.add_contact).pack(side="left", padx=4)

        tk.Label(root, text="Sent messages", bg=BG, fg="white",
                 font=("Segoe UI", 12, "bold")).pack(pady=(10, 2))
        self.log = tk.Text(root, height=10, wrap="word", font=("Segoe UI", 9))
        self.log.pack(fill="both", expand=True, padx=20, pady=(0, 15))

        self.update_gps()
        self.poll()

    # ---------- وظائف ----------
    def update_gps(self):
        self.gps.config(text=f"GPS: {self.lat:.5f}, {self.lon:.5f}")

    def add_contact(self):
        c = self.entry.get().strip()
        if c:
            self.contacts.append(c)
            self.listbox.insert("end", c)
            self.entry.delete(0, "end")

    def send_sms(self, reason):
        link = f"https://maps.google.com/?q={self.lat:.5f},{self.lon:.5f}"
        text = f"EMERGENCY: {reason}. Location: {link}"
        try:
            with open(os.path.join(BASE_DIR, "sent_alerts.txt"), "a", encoding="utf-8") as f:
                for c in self.contacts:
                    f.write(f"To {c}: {text}\n")
        except OSError as e:
            _log("save error:", e)
        self.log.insert("end", f"[Sent to {len(self.contacts)} contacts]\n{text}\n\n")
        self.log.see("end")
        telegram_send(text)

    # ---------- الكشف عن السقوط ----------
    def start_fall(self):
        if self.fall_left is not None:
            return
        self.fall_left = FALL_SECONDS
        self.fall_label.pack(pady=4)
        self.ok_btn.pack(fill="x")
        self.tick()

    def hide_fall(self):
        self.fall_label.pack_forget()
        self.ok_btn.pack_forget()

    def tick(self):
        if self.fall_left is None:
            return
        if self.fall_left <= 0:
            self.fall_left = None
            self.hide_fall()
            self.send_sms("Fall detected, no response")
            return
        self.fall_label.config(text=f"Fall detected! Sending alert in {self.fall_left}s")
        self.fall_left -= 1
        self.root.after(1000, self.tick)

    def cancel_fall(self):
        self.fall_left = None
        self.hide_fall()

    # ---------- استقبال الرسائل ----------
    def poll(self):
        try:
            while True:
                m = q.get_nowait()
                self.handle(m)
        except queue.Empty:
            pass
        except Exception as e:  # noqa: BLE001
            _log("poll error:", e)

        # لو الاتصال ساكت أكتر من 3 ثواني يبقى غالباً اتقطع
        if self.connected and time.monotonic() - self.last_rx > 3:
            self.set_connected(False)

        self.root.after(100, self.poll)

    def set_connected(self, state, msg=None):
        self.connected = state
        if state:
            self.last_rx = time.monotonic()
            self.status.config(text="Stick: connected", fg="lightgreen")
        else:
            self.status.config(text=msg or "Stick: not connected", fg="orange")

    def handle(self, m):
        t = m.get("type")
        if t == "error":
            self.set_connected(False, "Port busy: " + m.get("message", ""))
            return
        if t == "connected":
            self.set_connected(True)
            return
        if t == "disconnected":
            self.set_connected(False)
            return

        self.last_rx = time.monotonic()
        if not self.connected:
            self.set_connected(True)

        if t == "pos":
            px, py = m.get("x"), m.get("y")
            if px is None or py is None:
                return
            self.lat = BASE_LAT - (py - 500) * 0.00001
            self.lon = BASE_LON + (px - 80) * 0.00001
            self.update_gps()
        elif t == "alert":
            name = m.get("name")
            if name:
                txt = format_alert(name, m.get("dist"))
                self.alert.config(text=txt, fg="tomato")
                if self.speaker:
                    self.speaker.say(txt)
            else:
                self.alert.config(text="No alerts", fg="lightgreen")
        elif t == "fall":
            self.start_fall()

    def on_close(self):
        try:
            self.server.stop()
        finally:
            self.root.destroy()


def main():
    # يخلي العربي يطبع من غير ما يرمي UnicodeEncodeError على ترمنال ويندوز
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    speaker = Speaker() if PHONE_SPEAK else None
    threading.Thread(target=_telegram_worker, daemon=True).start()
    server = StickServer(HOST, PORT, q)
    server.start()

    root = tk.Tk()
    Phone(root, speaker, server)
    root.lift()
    root.attributes("-topmost", True)
    root.after(1000, lambda: root.attributes("-topmost", False))
    root.focus_force()
    root.mainloop()


if __name__ == "__main__":
    main()
