"""صوت التحذير: توليد وتخزين وتشغيل.

المشكلة القديمة:
- النص كان بيحتوي المسافة بالضبط (137 سم، 136 سم…) فكل سنتيمتر بيولّد ملف صوت جديد،
  يعني كل تنبيه فيه انتظار شبكة + أكتر من صوت بيشتغلوا فوق بعض.
- الحل: المسافة بتتقرب لشرائح 20 سم، فالملفات بتبقى محدودة وبتتخزن على الديسك،
  وبيتولّدها مرة واحدة بس (prewarm) قبل ما تحتاجها.
"""

import asyncio
import os
import queue
import threading
import time

VOICE = "ar-EG-SalmaNeural"
VOICE_RATE = "+40%"
BAND = 20            # شريحة المسافة بالسنتيمتر
MAX_DIST = 200

NAMES_AR = {"Car": "سيارة", "Person": "شخص", "Stairs": "سلم",
            "Chair": "كرسي", "Water": "مياه", "Pit": "حفرة"}


def warn_raw(name, d=None, lang="ar"):
    if lang == "ar":
        if d is None or d <= 0:
            return f"احذر! يوجد {NAMES_AR[name]} أمامك مباشرة"
        return f"احذر! يوجد {NAMES_AR[name]} على بعد {int(d)} سنتيمتر"
    if d is None or d <= 0:
        return f"Warning! {name} right ahead"
    return f"Warning! {name} ahead, {int(d)} centimeters"


def quantize(d, max_dist=MAX_DIST, band=BAND):
    """137 -> 140 ، 12 -> 20 ، 0 -> 0 (مباشرة)."""
    if d is None or d <= 0:
        return 0
    value = int(round(d / band)) * band
    return max(0, min(max_dist, value))


def band_text(name, band):
    """الجملة الكاملة زي اللي مكتوبة على الشاشة وفي الموبايل."""
    return warn_raw(name, band, "ar")


def choose_alert(hit_type, dist, warn_type=None, warn_dist=None):
    """تنبيه من أول ما الحساس يلاقي أي حاجة — مفيش شرط 180 سم.

    بيرجع (نوع العائق، شريحة المسافة) أو (None, None) لو الطريق فاضي.
    """
    if hit_type:
        return hit_type, quantize(dist)
    if warn_type:
        return warn_type, quantize(warn_dist)
    return None, None


def make_synth(voice=VOICE, rate=VOICE_RATE):
    """دالة التوليد الافتراضية (edge-tts، محتاج إنترنت)."""
    import edge_tts  # noqa: PLC0415

    def synth(text, path):
        async def gen():
            await edge_tts.Communicate(text, voice, rate=rate).save(path)

        asyncio.run(gen())

    return synth


class AlertGate:
    """بيقرر امتى نتكلم فعلاً.

    الجملة طويلة (حوالي 4.5 ثانية) والمسافة بتتغير بسرعة، فلو نطقناها وهي بتتحرك
    الرقم اللي بيتقال مش هو اللي على الشاشة. القاعدة:
      - عائق جديد  -> ننطق فوراً (الرقم وقتها هو اللي مكتوب)
      - غير كده    -> ننطق لما الرقم يثبت (المستخدم وقف أو بطّأ)
    """

    def __init__(self, stable_ms=350):
        self.stable_ms = stable_ms
        self._key = None
        self._since = 0
        self._announced = None

    def update(self, key, now_ms, type_changed=False):
        if key != self._key:
            self._key = key
            self._since = now_ms
        if key is None:
            self._announced = None
            return False
        if key == self._announced:
            return False
        if type_changed or now_ms - self._since >= self.stable_ms:
            self._announced = key
            return True
        return False


class VoiceEngine:
    """طابور صوت واحد: مفيش تشغيل متزامن، ومفيش انتظار جوه اللعبة."""

    def __init__(self, cache_dir, synth=None, player=None, dedupe=1.2, is_busy=None):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.synth = synth or make_synth()
        self.player = player
        self.is_busy = is_busy or (lambda: False)
        self.dedupe = dedupe
        self._q = queue.Queue()
        self._last_key = None
        self._last_time = 0.0
        self._stop = False
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, name="voice", daemon=True)
        self._worker.start()

    # ---------- للتجربة ----------
    def path_for(self, name, band):
        return os.path.join(self.cache_dir, f"{name}_{band}.mp3")

    def is_ready(self, name, band):
        p = self.path_for(name, band)
        return os.path.exists(p) and os.path.getsize(p) > 64

    # ---------- API ----------
    def speak(self, name, band):
        """بترجع True لو الصوت اتحط في الطابور (مش مكرر)."""
        band = quantize(band)
        key = (name, band)
        now = time.time()
        with self._lock:
            if key == self._last_key and now - self._last_time < self.dedupe:
                return False
            self._last_key = key
            self._last_time = now
        self._q.put(key)
        return True

    def prewarm(self, names=None, max_dist=MAX_DIST, workers=2):
        """بيولّد كل ملفات الأصوات في الخلفية قبل ما تحتاجها."""
        names = list(names or NAMES_AR)
        tasks = queue.Queue()
        for n in names:
            for b in range(0, max_dist + 1, BAND):
                if not self.is_ready(n, b):
                    tasks.put((n, b))
        for _ in range(workers):
            threading.Thread(target=self._prewarm_worker, args=(tasks,), daemon=True).start()

    def stop(self):
        self._stop = True

    # ---------- داخلي ----------
    def _prewarm_worker(self, tasks):
        while not self._stop:
            try:
                name, band = tasks.get_nowait()
            except queue.Empty:
                return
            try:
                self._ensure(name, band)
            except Exception as e:  # noqa: BLE001
                print("prewarm error:", e)

    def _ensure(self, name, band):
        path = self.path_for(name, band)
        if os.path.exists(path) and os.path.getsize(path) > 64:
            return path
        tmp = f"{path}.{threading.get_ident()}.part"
        self.synth(band_text(name, band), tmp)
        if not os.path.exists(tmp) or os.path.getsize(tmp) <= 64:
            raise OSError("ملف الصوت فاضي")
        os.replace(tmp, path)   # ملف مكتمل بس هو اللي بيظهر
        return path

    def _run(self):
        while not self._stop:
            key = self._q.get()

            # بناخد آخر رسالة بس: اللي فاتت بقت مسافة قديمة ومش هنقولها
            while True:
                try:
                    key = self._q.get_nowait()
                except queue.Empty:
                    break

            # لو لسه فيه جملة بتتنطق، نستنى تخلص وبناخد أحدث رقم وقتها
            while self.is_busy() and not self._stop:
                time.sleep(0.05)
                try:
                    key = self._q.get_nowait()
                except queue.Empty:
                    pass

            try:
                path = self._ensure(key[0], key[1])
                if self.player:
                    self.player(path)
            except Exception as e:  # noqa: BLE001
                print("Voice error:", e)
