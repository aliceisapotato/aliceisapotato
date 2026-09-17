"""Audio impact detection and motion statistics."""

import unittest

import numpy as np

from badminton_reels.audio import _moving_stats, pick_onsets
from badminton_reels.motion import (
    MotionTrack,
    _forward_fill,
    _stats_from_maps,
    estimate_roi,
    parse_roi,
)


class TestOnsets(unittest.TestCase):
    def strength_with_impacts(self, times, rate=62.5, duration=20.0, level=1000.0, noise=80.0):
        count = int(duration * rate)
        rng = np.random.default_rng(3)
        strength = noise + rng.normal(0, noise * 0.08, count)
        for t in times:
            strength[int(t * rate)] = level
        loudness = np.full(count, -60.0)
        for t in times:
            loudness[int(t * rate)] = -30.0
        return strength, loudness, rate

    def test_finds_every_impact(self):
        impacts = [1.0, 2.2, 3.1, 4.7, 6.0, 9.5, 12.25]
        strength, loudness, rate = self.strength_with_impacts(impacts)
        found = pick_onsets(strength, rate, loudness=loudness)
        self.assertEqual(found.size, len(impacts))
        for expected, actual in zip(impacts, found):
            self.assertAlmostEqual(expected, actual, delta=1.0 / rate + 1e-6)

    def test_noise_alone_produces_nothing(self):
        rng = np.random.default_rng(11)
        strength = 80.0 + rng.normal(0, 7.0, 2000)
        loudness = -60.0 + rng.normal(0, 0.5, 2000)
        self.assertEqual(pick_onsets(strength, 62.5, loudness=loudness).size, 0)

    def test_quiet_transients_are_gated_out(self):
        """A spectral spike that is not louder than the room is not a racket."""
        strength, loudness, rate = self.strength_with_impacts([2.0, 4.0])
        loudness[:] = -60.0  # nothing stands out in level
        self.assertEqual(pick_onsets(strength, rate, loudness=loudness).size, 0)

    def test_impacts_closer_than_the_minimum_gap_collapse(self):
        strength, loudness, rate = self.strength_with_impacts([5.0])
        strength[int(5.02 * rate)] = 900.0  # 20 ms later: the same hit
        loudness[int(5.02 * rate)] = -30.0
        self.assertEqual(pick_onsets(strength, rate, loudness=loudness).size, 1)

    def test_sensitivity_admits_weaker_hits(self):
        strength, loudness, rate = self.strength_with_impacts([3.0], level=1000.0)
        # A distant hit: sharp enough to see in the spectrum, but only just
        # above the room's own level, so the strict gate rejects it.
        strength[int(6.0 * rate)] = 400.0
        loudness[int(6.0 * rate)] = -57.5
        strict = pick_onsets(strength, rate, loudness=loudness)
        loose = pick_onsets(strength, rate, loudness=loudness,
                            global_relative=0.15 / 2.0, min_gain_db=4.0 / 2.0,
                            local_factor=1.5 / 2.0)
        self.assertGreater(loose.size, strict.size)

    def test_moving_stats_track_a_step(self):
        values = np.concatenate([np.zeros(100), np.ones(100)])
        mean, std = _moving_stats(values, 11)
        self.assertAlmostEqual(mean[10], 0.0, places=6)
        self.assertAlmostEqual(mean[-10], 1.0, places=6)
        self.assertGreater(std[100], 0.0)

    def test_empty_input_is_safe(self):
        self.assertEqual(pick_onsets(np.zeros(0), 62.5).size, 0)
        self.assertEqual(pick_onsets(np.ones(10), 0.0).size, 0)


class TestRoi(unittest.TestCase):
    def test_finds_the_busy_band(self):
        heat = np.zeros((90, 160))
        heat[30:60, 40:120] = 1.0          # the court
        x, y, w, h = estimate_roi(heat, margin=0.0)
        self.assertAlmostEqual(x, 0.25, delta=0.05)
        self.assertAlmostEqual(x + w, 0.75, delta=0.05)
        self.assertAlmostEqual(y, 0.333, delta=0.05)
        self.assertAlmostEqual(y + h, 0.667, delta=0.05)

    def test_a_lone_mover_does_not_stretch_the_box(self):
        heat = np.zeros((90, 160))
        heat[40:50, 60:100] = 1.0          # play
        heat[5:8, 155:159] = 0.4           # someone walking past a far corner
        x, y, w, h = estimate_roi(heat, margin=0.0)
        self.assertLess(x + w, 0.95)
        self.assertGreater(y, 0.05)

    def test_a_flat_heat_map_keeps_the_whole_frame(self):
        self.assertEqual(estimate_roi(np.zeros((10, 10))), (0.0, 0.0, 1.0, 1.0))

    def test_never_collapses_below_the_minimum(self):
        heat = np.zeros((90, 160))
        heat[45, 80] = 1.0
        _, _, w, h = estimate_roi(heat, margin=0.0, min_size=0.25)
        self.assertGreaterEqual(w, 0.25)
        self.assertGreaterEqual(h, 0.25)

    def test_parse_roi_accepts_fractions_and_percentages(self):
        self.assertEqual(parse_roi("0.1,0.2,0.5,0.6"), (0.1, 0.2, 0.5, 0.6))
        x, y, w, h = parse_roi("10%,20%,50%,60%")
        self.assertAlmostEqual(x, 0.1)
        self.assertAlmostEqual(h, 0.6)
        with self.assertRaises(ValueError):
            parse_roi("1,2,3")

    def test_parse_roi_clamps_to_the_frame(self):
        x, y, w, h = parse_roi("0.8,0.8,0.5,0.5")
        self.assertAlmostEqual(x + w, 1.0)
        self.assertAlmostEqual(y + h, 1.0)


class TestMotionStats(unittest.TestCase):
    def test_centroid_follows_the_moving_blob(self):
        maps = np.zeros((3, 9, 16), dtype=np.uint8)
        maps[0, 4, 2] = 200
        maps[1, 4, 8] = 200
        maps[2, 4, 13] = 200
        energy, cx, cy = _stats_from_maps(maps, None)
        self.assertTrue(np.all(np.diff(cx) > 0))
        self.assertTrue(np.allclose(cy, 4.5 / 9, atol=0.01))
        self.assertTrue(np.all(energy > 0))

    def test_region_of_interest_masks_motion_out(self):
        maps = np.zeros((2, 9, 16), dtype=np.uint8)
        maps[:, 1, 1] = 255                     # movement in a top-left corner
        inside = _stats_from_maps(maps, None)[0]
        outside = _stats_from_maps(maps, (0.5, 0.5, 0.5, 0.5))[0]
        self.assertGreater(inside[0], 0.0)
        self.assertEqual(outside[0], 0.0)

    def test_idle_frames_hold_the_last_position(self):
        maps = np.zeros((4, 9, 16), dtype=np.uint8)
        maps[0, 4, 12] = 200                    # one busy frame, then stillness
        _, cx, _ = _stats_from_maps(maps, None)
        self.assertTrue(np.allclose(cx, cx[0]))

    def test_forward_fill_uses_the_first_real_value(self):
        filled = _forward_fill(np.array([np.nan, np.nan, 0.8, np.nan]), 0.5)
        self.assertTrue(np.allclose(filled, [0.8, 0.8, 0.8, 0.8]))

    def test_track_sampling_and_windowing(self):
        track = MotionTrack(
            sample_rate=10.0,
            energy=np.linspace(0, 1, 100),
            cx=np.linspace(0, 1, 100),
            cy=np.full(100, 0.5),
            heat=np.zeros((9, 16)),
        )
        self.assertAlmostEqual(track.duration, 10.0)
        energy, cx, _ = track.sample_at(5.0)
        self.assertAlmostEqual(cx, 0.5, delta=0.02)
        times, xs, _ = track.window(2.0, 3.0)
        self.assertGreater(times.size, 5)
        self.assertGreaterEqual(times[0], 2.0)
        self.assertLessEqual(times[-1], 3.2)
        self.assertEqual(times.size, xs.size)

    def test_sampling_past_the_end_is_clamped(self):
        track = MotionTrack(10.0, np.ones(10), np.full(10, 0.3), np.full(10, 0.7),
                            np.zeros((9, 16)))
        self.assertAlmostEqual(track.sample_at(999.0)[1], 0.3)


if __name__ == "__main__":
    unittest.main()
