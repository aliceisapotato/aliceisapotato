"""Vertical framing: crop geometry and the smoothness of the pan path."""

import unittest

import numpy as np

from badminton_reels.framing import (
    Canvas,
    PanParams,
    ema_zero_phase,
    even,
    fit_rect,
    limit_speed,
    median_filter,
    plan_frame,
    plan_pan,
    sendcmd_script,
)

SOURCE = (3840, 2160)  # what an iPhone shoots in 4K
CANVAS = Canvas(1080, 1920)


class TestGeometry(unittest.TestCase):
    def test_nine_by_sixteen_crop_of_a_16_9_frame(self):
        width, height = fit_rect(*SOURCE, aspect=CANVAS.aspect)
        self.assertEqual(height, 2160)            # full source height
        self.assertAlmostEqual(width / height, CANVAS.aspect, places=2)
        self.assertEqual(width % 2, 0)

    def test_zoom_in_tightens_and_keeps_the_aspect(self):
        base_w, base_h = fit_rect(*SOURCE, aspect=CANVAS.aspect)
        tight_w, tight_h = fit_rect(*SOURCE, aspect=CANVAS.aspect, zoom=1.5)
        self.assertLess(tight_w, base_w)
        self.assertLess(tight_h, base_h)
        self.assertAlmostEqual(tight_w / tight_h, CANVAS.aspect, places=2)

    def test_zoom_out_is_clamped_to_the_frame(self):
        width, height = fit_rect(*SOURCE, aspect=CANVAS.aspect, zoom=0.25)
        self.assertLessEqual(width, SOURCE[0])
        self.assertLessEqual(height, SOURCE[1])

    def test_even_rounds_sizes_down_for_chroma(self):
        self.assertEqual(even(101), 100)
        self.assertEqual(even(0), 2)          # never a zero-sized crop
        self.assertEqual(even(607.5), 606)

    def test_offsets_may_be_zero_and_stay_in_range(self):
        from badminton_reels.framing import offset

        self.assertEqual(offset(0.0, 100), 0)
        self.assertEqual(offset(-5.0, 100), 0)
        self.assertEqual(offset(101.0, 100), 100)
        self.assertEqual(offset(50.6, 100), 50)


class TestPlans(unittest.TestCase):
    def plan(self, layout, **kwargs):
        times = np.linspace(0, 10, 121)
        cx = 0.5 + 0.3 * np.sin(times)
        return plan_frame(
            source_w=SOURCE[0],
            source_h=SOURCE[1],
            duration=10.0,
            canvas=CANVAS,
            layout=layout,
            sample_times=times,
            cx=cx,
            cy=np.full_like(times, 0.5),
            **kwargs,
        )

    def test_follow_fills_the_canvas_without_bars(self):
        plan = self.plan("follow")
        self.assertEqual(len(plan.panes), 1)
        pane = plan.panes[0]
        self.assertEqual((pane.scaled_w, pane.scaled_h), (1080, 1920))
        self.assertFalse(plan.blur_background)
        self.assertTrue(pane.animated)

    def test_fit_letterboxes_over_a_blurred_bed(self):
        plan = self.plan("fit")
        pane = plan.panes[0]
        self.assertTrue(plan.blur_background)
        self.assertEqual(pane.scaled_w, 1080)
        self.assertLess(pane.scaled_h, 1920)
        self.assertFalse(pane.animated)  # the whole frame is shown, nothing to track

    def test_stack_covers_the_canvas_with_two_panes(self):
        plan = self.plan("stack")
        self.assertEqual(len(plan.panes), 2)
        wide, close = plan.panes
        self.assertEqual(wide.dest_y, 0)
        self.assertGreater(close.dest_y, wide.scaled_h - 2)
        self.assertLessEqual(close.dest_y + close.scaled_h, 1920)
        self.assertTrue(close.animated)
        self.assertFalse(wide.animated)

    def test_pan_none_holds_still(self):
        plan = self.plan("follow", pan=PanParams(mode="none"))
        self.assertFalse(plan.panes[0].animated)

    def test_crop_never_leaves_the_frame(self):
        plan = self.plan("follow")
        pane = plan.panes[0]
        for x, y in zip(pane.xs, pane.ys):
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + pane.crop_w, SOURCE[0])
            self.assertLessEqual(y + pane.crop_h, SOURCE[1])

    def test_unknown_layout_is_rejected(self):
        with self.assertRaises(ValueError):
            self.plan("sideways")


class TestSmoothing(unittest.TestCase):
    def test_median_filter_removes_a_single_spike(self):
        values = np.array([1.0, 1.0, 9.0, 1.0, 1.0])
        self.assertTrue(np.allclose(median_filter(values, 3), 1.0))

    def test_median_filter_keeps_length(self):
        values = np.arange(10.0)
        self.assertEqual(median_filter(values, 5).size, 10)

    def test_zero_phase_ema_does_not_lag(self):
        """A ramp survives smoothing without being shifted sideways."""
        ramp = np.linspace(0.0, 100.0, 200)
        smoothed = ema_zero_phase(ramp, 0.2)
        self.assertLess(abs(smoothed[100] - ramp[100]), 2.0)

    def test_speed_limit_bounds_every_step(self):
        jumpy = np.array([0.0, 100.0, 0.0, 100.0, 0.0])
        limited = limit_speed(jumpy, 5.0)
        self.assertTrue(np.all(np.abs(np.diff(limited)) <= 5.0 + 1e-9))

    def test_pan_path_is_clamped_and_smooth(self):
        times = np.linspace(0, 10, 121)
        # A target that teleports across the court every sample.
        centers = np.where(np.arange(121) % 2 == 0, 0, 3840)
        out_times = np.arange(0, 10, 1 / 30.0)
        path = plan_pan(
            times,
            centers,
            out_times=out_times,
            span=1216,
            limit=3840 - 1216,
            pan=PanParams(),
            frame_center=1920.0,
        )
        self.assertTrue(np.all(path >= 0))
        self.assertTrue(np.all(path <= 3840 - 1216))
        step = PanParams().max_speed * 1216 / 30.0
        self.assertTrue(np.all(np.abs(np.diff(path)) <= step + 1e-6))

    def test_pan_path_with_no_limit_stays_at_zero(self):
        out_times = np.arange(0, 2, 0.1)
        path = plan_pan([0, 1], [100, 200], out_times=out_times, span=1920,
                        limit=0, pan=PanParams(), frame_center=960.0)
        self.assertTrue(np.allclose(path, 0.0))


class TestSendcmd(unittest.TestCase):
    def test_script_only_emits_changes(self):
        plan = plan_frame(
            source_w=SOURCE[0], source_h=SOURCE[1], duration=4.0, canvas=CANVAS,
            layout="follow",
            sample_times=[0, 1, 2, 3, 4],
            cx=[0.2, 0.2, 0.8, 0.8, 0.8],
            cy=[0.5] * 5,
            command_rate=10.0,
        )
        pane = plan.panes[0]
        script = sendcmd_script(pane, "crop@p0")
        lines = [line for line in script.splitlines() if line]
        self.assertTrue(lines)
        self.assertTrue(all(line.endswith(";") for line in lines))
        self.assertTrue(all("crop@p0 " in line for line in lines))
        # Deduplicated: the initial position is already on the crop filter,
        # and repeated positions emit nothing.
        self.assertLess(len(lines), len(pane.times))
        stamps = [float(line.split(" ", 1)[0]) for line in lines]
        self.assertEqual(stamps, sorted(stamps))
        self.assertGreater(stamps[0], 0.0)

    def test_static_pane_needs_no_script(self):
        plan = plan_frame(
            source_w=SOURCE[0], source_h=SOURCE[1], duration=2.0, canvas=CANVAS,
            layout="follow", pan=PanParams(mode="none"),
            sample_times=[0, 1, 2], cx=[0.5, 0.5, 0.5], cy=[0.5, 0.5, 0.5],
        )
        self.assertEqual(sendcmd_script(plan.panes[0]), "")


if __name__ == "__main__":
    unittest.main()
