import array
import csv
import json
import math
import os
import random
import sys
import tempfile
import time

import pygame

from stick_audio import AlertGate, VoiceEngine, choose_alert, quantize, warn_raw
from stick_link import StickLink

# يخلي العربي يطبع من غير UnicodeEncodeError على ترمنال ويندوز
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

try:
    import edge_tts  # noqa: F401
    HAS_EDGE = True
except ImportError:
    HAS_EDGE = False

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    USE_AR = True
except ImportError:
    USE_AR = False


# ---------- مسار المشروع (بيشتغل سواء من .py أو من .exe) ----------
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config():
    try:
        with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


CONFIG = load_config()
PORT = int(os.environ.get("STICK_PORT", CONFIG.get("port", 5005)))
HOST = CONFIG.get("host", "127.0.0.1")
PC_VOICE = CONFIG.get("voice", {}).get("pc", True)


def show(name, d=None):
    if USE_AR:
        return get_display(arabic_reshaper.reshape(warn_raw(name, d, "ar")))
    return warn_raw(name, d, "en")


pygame.init()
W, H = 900, 600
screen = pygame.display.set_mode((W, H), pygame.RESIZABLE | pygame.SCALED)
pygame.display.set_caption("Smart Blind Stick Simulation")
clock = pygame.time.Clock()
pygame.mixer.init(frequency=44100, size=-16, channels=1)
pygame.mixer.set_num_channels(8)
pygame.mixer.set_reserved(1)          # أول قناة محجوزة للكلام
voice_channel = pygame.mixer.Channel(0)
font = pygame.font.SysFont("arial", 18)
big = pygame.font.SysFont("arial", 24, bold=True)

MAX_RANGE = 200
SAFE_DISTANCE = 180  # للعرض بس دلوقتي — التنبيه بقى من أول ما الحساس يلاقي حاجة


# ---------- البيبة ----------
def make_beep(freq, ms):
    n = int(44100 * ms / 1000)
    wave = array.array(
        "h", [int(12000 * math.sin(2 * math.pi * freq * i / 44100)) for i in range(n)]
    )
    return pygame.mixer.Sound(buffer=wave.tobytes())


beeps = {
    "Car": make_beep(1200, 80),
    "Person": make_beep(1000, 80),
    "Stairs": make_beep(900, 80),
    "Chair": make_beep(700, 80),
}
default_beep = make_beep(800, 80)
hazard_beeps = {"Water": make_beep(400, 200), "Pit": make_beep(250, 300)}
last_beep = 0
last_hazard_beep = 0


# ---------- الكلام ----------
sound_cache = {}


def play_voice(path):
    """قناة واحدة للكلام — الترتيب والاستبدال بيتحكموا من VoiceEngine."""
    try:
        snd = sound_cache.get(path)
        if snd is None:
            snd = pygame.mixer.Sound(path)
            sound_cache[path] = snd
        voice_channel.play(snd)
    except Exception as e:  # noqa: BLE001
        print("Voice play error:", e)


voice = None
if PC_VOICE and HAS_EDGE:
    # ملفات الأصوات بتتخزن على الديسك، فبعد أول مرة بتشتغل فوراً
    voice = VoiceEngine(
        os.path.join(BASE_DIR, "voice_cache"),
        player=play_voice,
        is_busy=lambda: voice_channel.get_busy(),
    )
    voice.prewarm()   # بيولّد كل الشرائح في الخلفية
elif PC_VOICE:
    print("ملاحظة: عشان يشتغل الصوت سطّبي edge-tts  ->  pip install edge-tts")


# ---------- اللوج ----------
def open_log():
    path = os.path.join(BASE_DIR, "log.csv")
    try:
        return open(path, "w", newline="", encoding="utf-8")
    except OSError:
        return open(os.path.join(tempfile.gettempdir(), "stick_log.csv"), "w",
                    newline="", encoding="utf-8")


log_file = open_log()
log = csv.writer(log_file)
log.writerow(["time_s", "event", "distance_cm"])
start_time = time.time()


def log_event(name, d):
    log.writerow([round(time.time() - start_time, 1), name, d])
    log_file.flush()


# ---------- الاتصال ----------
link = StickLink(HOST, PORT).start()
send = link.send
last_send = 0


obstacles = [
    {"rect": pygame.Rect(300, 120, 60, 60), "type": "Chair", "color": (160, 110, 60)},
    {"rect": pygame.Rect(450, 440, 120, 60), "type": "Car", "color": (200, 60, 60)},
    {"rect": pygame.Rect(200, 300, 80, 40), "type": "Stairs", "color": (150, 150, 150)},
    {"rect": pygame.Rect(100, 40, 120, 50), "type": "Car", "color": (200, 60, 60),
     "move": {"axis": "x", "speed": 2, "lo": 50, "hi": 700}},
    {"rect": pygame.Rect(720, 120, 40, 40), "type": "Person", "color": (230, 190, 60),
     "move": {"axis": "y", "speed": 2, "lo": 100, "hi": 320}},
]

hazards = [
    {"rect": pygame.Rect(600, 380, 180, 100), "type": "Water", "color": (40, 110, 220)},
    {"rect": pygame.Rect(320, 330, 110, 80), "type": "Pit", "color": (10, 10, 10)},
]


def update_obstacles():
    for o in obstacles:
        m = o.get("move")
        if not m:
            continue
        if m["axis"] == "x":
            o["rect"].x += m["speed"]
            if o["rect"].x < m["lo"] or o["rect"].x > m["hi"]:
                m["speed"] *= -1
        else:
            o["rect"].y += m["speed"]
            if o["rect"].y < m["lo"] or o["rect"].y > m["hi"]:
                m["speed"] *= -1


START = (80.0, 500.0)
x, y = START
angle = 0.0
RADIUS = 14
STICK_LEN = 45
noise_on = False
last_alert = None
last_alert_dist = 0
STABLE_MS = 350          # لازم الرقم يثبت كده قبل ما ينطقه
gate = AlertGate(STABLE_MS)
history = []


def ultrasonic(px, py, ang):
    dx, dy = math.cos(ang), math.sin(ang)
    for d in range(0, MAX_RANGE, 2):
        sx, sy = px + dx * d, py + dy * d
        if not (0 <= sx < W and 0 <= sy < H):
            break
        for o in obstacles:
            if o["rect"].collidepoint(sx, sy):
                return d, o
    return MAX_RANGE, None


def hazard_scan(px, py, ang, max_d=120):
    dx, dy = math.cos(ang), math.sin(ang)
    for d in range(0, max_d, 2):
        sx, sy = px + dx * d, py + dy * d
        if not (0 <= sx < W and 0 <= sy < H):
            break
        for h in hazards:
            if h["rect"].collidepoint(sx, sy):
                return d, h
    return None, None


fell_until = 0

running = True
try:
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                if event.key == pygame.K_r:
                    x, y = START
                    angle = 0.0
                    last_alert = None
                if event.key == pygame.K_n:
                    noise_on = not noise_on
                if event.key == pygame.K_f:
                    send({"type": "fall"})
                if event.key == pygame.K_F11:
                    pygame.display.toggle_fullscreen()

        update_obstacles()

        keys = pygame.key.get_pressed()
        if keys[pygame.K_LEFT]:
            angle -= 3
        if keys[pygame.K_RIGHT]:
            angle += 3
        step = 0
        if keys[pygame.K_UP]:
            step = 2.5
        if keys[pygame.K_DOWN]:
            step = -2.5

        rad = math.radians(angle)
        nx = x + math.cos(rad) * step
        ny = y + math.sin(rad) * step
        body = pygame.Rect(nx - RADIUS, ny - RADIUS, RADIUS * 2, RADIUS * 2)
        if body.collidelist([o["rect"] for o in obstacles]) == -1 and 0 < nx < W and 0 < ny < H:
            x, y = nx, ny

        # ---------- الحساسات ----------
        tip = (x + math.cos(rad) * STICK_LEN, y + math.sin(rad) * STICK_LEN)
        dist, hit = ultrasonic(x, y, rad)
        if noise_on and hit:
            dist = max(0, dist + random.randint(-2, 2))
        # رقم واحد لكل حاجة: الشاشة + الموبايل + الصوت
        dist = quantize(dist)
        hazard = next((h for h in hazards if h["rect"].collidepoint(tip)), None)
        h_dist, h_ahead = hazard_scan(x, y, rad)
        if h_dist is not None:
            h_dist = quantize(h_dist)
        if any(h["type"] == "Pit" and h["rect"].collidepoint(x, y) for h in hazards):
            x, y = START
            angle = 0.0
            fell_until = pygame.time.get_ticks() + 2000
            log_event("FELL_IN_PIT", "-")
            send({"type": "fall"})
        intensity = 0 if dist >= MAX_RANGE else 1 - dist / MAX_RANGE

        history.append(dist)
        if len(history) > 200:
            history.pop(0)

        # ---------- الصوت والاتصال ----------
        now = pygame.time.get_ticks()

        if now - last_send > 200:
            send({"type": "pos", "x": x, "y": y})
            last_send = now

        # بيبة متقطعة أسرع كل ما تقرب
        if intensity > 0 and hit:
            interval = 100 + int(700 * (1 - intensity))
            if now - last_beep > interval:
                beeps.get(hit["type"], default_beep).play()
                last_beep = now

        # بيبة المية والحفرة
        warn = hazard or h_ahead
        if warn:
            gap = 150 if hazard else 300 + (h_dist or 0) * 6
            if now - last_hazard_beep > gap:
                hazard_beeps.get(warn["type"], default_beep).play()
                last_hazard_beep = now

        # ---------- التنبيه ----------
        # أول ما الحساس يلاقي أي حاجة (لحد 200 سم) بننبه فوراً.
        alert, alert_dist = choose_alert(
            hit["type"] if hit else None,
            dist,
            warn["type"] if warn else None,
            h_dist,
        )

        # بننطق تاني بس لما نوع العائق أو شريحة المسافة تتغير (مش كل سنتيمتر)
        key = (alert, alert_dist) if alert else None

        # الصوت: فوراً لما يظهر عائق جديد، وبعد كده لما الرقم يثبت
        # كده الرقم اللي بيتنطق هو نفسه اللي مكتوب على الشاشة في نفس اللحظة
        if voice and gate.update(key, now, type_changed=(alert != last_alert)):
            voice.speak(alert, alert_dist)

        if alert != last_alert or (alert is not None and alert_dist != last_alert_dist):
            if alert:
                log_event(alert, alert_dist)
                send({"type": "alert", "name": alert, "dist": alert_dist})
            elif last_alert is not None:
                send({"type": "alert", "name": None, "dist": None})

            last_alert = alert
            last_alert_dist = alert_dist if alert_dist is not None else 0

        # ---------- الرسم ----------
        screen.fill((40, 40, 40))
        for h in hazards:
            pygame.draw.rect(screen, h["color"], h["rect"])
            if h["type"] == "Pit":
                pygame.draw.rect(screen, (220, 40, 40), h["rect"], 3)
            screen.blit(font.render(h["type"], True, (255, 255, 255)), (h["rect"].x + 5, h["rect"].y + 5))
        for o in obstacles:
            pygame.draw.rect(screen, o["color"], o["rect"])
            screen.blit(font.render(o["type"], True, (255, 255, 255)), (o["rect"].x, o["rect"].y - 20))

        end = (x + math.cos(rad) * dist, y + math.sin(rad) * dist)
        pygame.draw.line(screen, (0, 255, 120), (x, y), end, 1)
        pygame.draw.line(screen, (255, 255, 255), (x, y), tip, 4)
        pygame.draw.circle(screen, (0, 200, 255), (int(x), int(y)), RADIUS)

        screen.blit(big.render(f"Distance: {dist} cm", True, (255, 255, 255)), (15, 10))
        vib_color = (255, int(255 * (1 - intensity)), 0)
        pygame.draw.rect(screen, (90, 90, 90), (15, 50, 200, 18))
        pygame.draw.rect(screen, vib_color, (15, 50, int(200 * intensity), 18))
        screen.blit(font.render("Vibration", True, (255, 255, 255)), (225, 50))
        if warn:
            txt = show(warn["type"], h_dist)
            screen.blit(big.render(txt, True, (80, 170, 255)), (15, 80))
        if pygame.time.get_ticks() < fell_until:
            screen.blit(big.render("YOU FELL IN THE PIT!", True, (255, 60, 60)), (W // 2 - 130, H // 2))
        if hit:
            msg = show(hit["type"], dist)
            color = (255, 120, 120) if dist < SAFE_DISTANCE else (255, 210, 120)
            screen.blit(big.render(msg, True, color), (15, 110))

        gx, gy, gw, gh = W - 230, 10, 220, 80
        pygame.draw.rect(screen, (25, 25, 25), (gx, gy, gw, gh))
        pygame.draw.rect(screen, (120, 120, 120), (gx, gy, gw, gh), 1)
        if len(history) > 1:
            pts = [
                (gx + i * gw / 200, gy + gh - d / MAX_RANGE * gh)
                for i, d in enumerate(history)
            ]
            pygame.draw.lines(screen, (0, 255, 120), False, pts, 2)
        screen.blit(font.render("Distance graph", True, (180, 180, 180)), (gx, gy + gh + 2))

        status = "ON" if noise_on else "OFF"
        screen.blit(
            font.render(f"Arrows: move/rotate | R: reset | N: noise ({status}) | F: fall | Esc: quit",
                        True, (180, 180, 180)),
            (15, H - 30),
        )

        pygame.display.flip()
        clock.tick(60)
finally:
    try:
        send({"type": "alert", "name": None, "dist": None})
        time.sleep(0.05)
        link.stop()
        if voice:
            voice.stop()
    except Exception:
        pass
    try:
        log_file.close()
    except Exception:
        pass
    pygame.quit()
