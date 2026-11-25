"""اختبارات الصوت: تقريب المسافة، التخزين، ومنع التكرار."""

import os
import shutil
import sys
import time
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stick_audio  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def wait_for(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestQuantize(unittest.TestCase):
    def test_rounds_to_bands(self):
        self.assertEqual(stick_audio.quantize(137), 140)
        self.assertEqual(stick_audio.quantize(12), 20)
        self.assertEqual(stick_audio.quantize(103), 100)

    def test_zero_and_none_mean_directly_ahead(self):
        self.assertEqual(stick_audio.quantize(0), 0)
        self.assertEqual(stick_audio.quantize(None), 0)

    def test_clamped_to_sensor_range(self):
        self.assertEqual(stick_audio.quantize(500), 200)

    def test_text_is_the_full_sentence(self):
        self.assertEqual(stick_audio.band_text("Car", 120),
                         "احذر! يوجد سيارة على بعد 120 سنتيمتر")
        self.assertEqual(stick_audio.band_text("Car", 0),
                         "احذر! يوجد سيارة أمامك مباشرة")


class TestChooseAlert(unittest.TestCase):
    """أهم شرط عندها: التنبيه يشتغل من أول ما يلاقي حاجة، مش بس تحت 180."""

    def test_far_obstacle_still_alerts(self):
        self.assertEqual(stick_audio.choose_alert("Car", 198), ("Car", 200))

    def test_179_is_not_a_threshold(self):
        self.assertEqual(stick_audio.choose_alert("Car", 179), ("Car", 180))

    def test_hazard_also_alerts(self):
        self.assertEqual(stick_audio.choose_alert(None, 200, "Water", 80), ("Water", 80))

    def test_nothing_ahead(self):
        self.assertEqual(stick_audio.choose_alert(None, 200), (None, None))


class TestAlertGate(unittest.TestCase):
    """امتى ينطق: فوراً لعائق جديد، وبعد كده لما الرقم يثبت بس."""

    def setUp(self):
        self.gate = stick_audio.AlertGate(stable_ms=350)

    def test_new_obstacle_is_announced_immediately(self):
        self.assertTrue(self.gate.update(("Car", 200), 0, type_changed=True))

    def test_distance_changing_while_walking_is_not_announced(self):
        self.assertTrue(self.gate.update(("Car", 200), 0, type_changed=True))
        self.assertFalse(self.gate.update(("Car", 180), 130))   # لسه بيتحرك
        self.assertFalse(self.gate.update(("Car", 160), 260))
        self.assertFalse(self.gate.update(("Car", 140), 390))

    def test_announces_when_number_settles(self):
        self.gate.update(("Car", 140), 0, type_changed=True)
        self.assertFalse(self.gate.update(("Car", 120), 130))
        self.assertFalse(self.gate.update(("Car", 120), 300))
        self.assertTrue(self.gate.update(("Car", 120), 500))     # ثبت 370ms

    def test_clearing_resets(self):
        self.assertTrue(self.gate.update(("Car", 200), 0, type_changed=True))
        self.assertFalse(self.gate.update(None, 100))
        self.assertTrue(self.gate.update(("Car", 200), 500, type_changed=True))

    def test_same_band_not_repeated(self):
        self.gate.update(("Car", 120), 0, type_changed=True)
        self.assertFalse(self.gate.update(("Car", 120), 1000))


class FakeSynth:
    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first

    def __call__(self, text, path):
        self.calls.append((text, path))
        if self.fail_first:
            self.fail_first = False
            raise OSError("network down")
        with open(path, "wb") as f:
            f.write(b"\0" * 200)


class TestVoiceEngine(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.join(HERE, "voice_test_" + uuid.uuid4().hex[:8])
        os.makedirs(self.dir, exist_ok=True)
        self.played = []
        self.synth = FakeSynth()
        self.engine = stick_audio.VoiceEngine(
            self.dir, synth=self.synth, player=self.played.append, dedupe=0.5
        )

    def tearDown(self):
        self.engine.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_speaks_and_plays(self):
        self.assertTrue(self.engine.speak("Car", 120))
        self.assertTrue(wait_for(lambda: len(self.played) == 1))
        self.assertTrue(os.path.exists(self.engine.path_for("Car", 120)))

    def test_same_alert_is_not_repeated_immediately(self):
        self.assertTrue(self.engine.speak("Car", 120))
        self.assertFalse(self.engine.speak("Car", 120), "مينفعش يتنطق مرتين ورا بعض")
        self.assertTrue(wait_for(lambda: len(self.played) == 1))

    def test_new_band_speaks_again(self):
        self.engine.speak("Car", 160)
        self.assertTrue(wait_for(lambda: len(self.played) == 1))
        self.assertTrue(self.engine.speak("Car", 120))
        self.assertTrue(wait_for(lambda: len(self.played) == 2))

    def test_cache_is_reused(self):
        self.engine.speak("Pit", 40)
        self.assertTrue(wait_for(lambda: len(self.played) == 1))
        calls = len(self.synth.calls)
        self.engine.speak("Pit", 80)
        self.assertTrue(wait_for(lambda: len(self.played) == 2))
        self.assertEqual(len(self.synth.calls), calls + 1)

    def test_synth_error_does_not_kill_worker(self):
        broken = stick_audio.VoiceEngine(
            self.dir, synth=FakeSynth(fail_first=True), player=self.played.append, dedupe=0.1
        )
        try:
            broken.speak("Car", 40)
            time.sleep(0.3)
            broken.speak("Car", 80)
            self.assertTrue(wait_for(lambda: len(self.played) == 1), "لازم يكمل بعد الخطأ")
        finally:
            broken.stop()

    def test_only_the_latest_distance_is_played(self):
        """لو المسافة اتغيرت كذا مرة وهو بيتكلم، ينطق آخر رقم بس."""
        state = {"busy": True}
        eng = stick_audio.VoiceEngine(
            self.dir, synth=FakeSynth(), player=self.played.append,
            dedupe=0.0, is_busy=lambda: state["busy"],
        )
        try:
            eng.speak("Car", 120)
            eng.speak("Car", 100)
            eng.speak("Car", 80)
            time.sleep(0.2)
            state["busy"] = False
            self.assertTrue(wait_for(lambda: len(self.played) == 1))
            self.assertTrue(self.played[0].endswith("Car_80.mp3"),
                            f"لازم ينطق الأحدث، طلع: {self.played[0]}")
        finally:
            eng.stop()

    def test_prewarm_fills_cache(self):
        self.engine.prewarm(names=["Car"], max_dist=80, workers=1)
        self.assertTrue(wait_for(lambda: len(self.synth.calls) == 5, 5.0))
        for band in (0, 20, 40, 60, 80):
            self.assertTrue(self.engine.is_ready("Car", band))


if __name__ == "__main__":
    unittest.main(verbosity=2)
