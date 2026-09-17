"""CLI argument plumbing. Nothing here touches ffmpeg."""

import unittest

from badminton_reels.cli import (
    Console,
    analysis_settings_from_args,
    build_parser,
    detect_params_from_args,
    parse_ids,
    parse_size,
    render_options_from_args,
    wants_labels,
)
from badminton_reels.render import RenderOptions


def parse(*argv):
    return build_parser().parse_args(list(argv))


class TestParsing(unittest.TestCase):
    def test_ids_accept_bare_numbers(self):
        self.assertEqual(parse_ids("r01,r03"), ["r01", "r03"])
        self.assertEqual(parse_ids("1,3"), ["r01", "r03"])
        self.assertEqual(parse_ids("r01 r02"), ["r01", "r02"])
        self.assertEqual(parse_ids(None), [])
        self.assertEqual(parse_ids(""), [])

    def test_size_parsing(self):
        canvas = parse_size("1080x1920")
        self.assertEqual((canvas.width, canvas.height), (1080, 1920))
        with self.assertRaises(Exception):
            parse_size("1080")

    def test_quiet_works_before_and_after_the_subcommand(self):
        self.assertTrue(parse("-q", "list", "p.json").quiet)
        self.assertTrue(parse("list", "p.json", "-q").quiet)
        self.assertFalse(parse("list", "p.json").quiet)

    def test_ffmpeg_output_works_after_the_subcommand(self):
        self.assertTrue(parse("render", "p.json", "--ffmpeg-output").ffmpeg_output)
        self.assertFalse(parse("render", "p.json").ffmpeg_output)


class TestDetectorFlags(unittest.TestCase):
    def test_defaults_are_untouched(self):
        params = detect_params_from_args(parse("analyze", "v.mov"))
        self.assertAlmostEqual(params.min_duration, 2.0)
        self.assertEqual(params.min_shots, 2)

    def test_flags_override_defaults(self):
        params = detect_params_from_args(
            parse("analyze", "v.mov", "--min-rally", "4", "--min-shots", "1",
                  "--pre", "0.2", "--post", "2.5", "--enter", "0.3", "--exit", "0.2")
        )
        self.assertAlmostEqual(params.min_duration, 4.0)
        self.assertEqual(params.min_shots, 1)
        self.assertAlmostEqual(params.pre_roll, 0.2)
        self.assertAlmostEqual(params.post_roll, 2.5)
        self.assertAlmostEqual(params.enter_level, 0.3)
        self.assertAlmostEqual(params.exit_level, 0.2)

    def test_analysis_settings(self):
        settings = analysis_settings_from_args(
            parse("analyze", "v.mov", "--region", "10%,20%,50%,40%", "--no-audio",
                  "--sensitivity", "1.5", "--sample-rate", "8")
        )
        self.assertTrue(settings.skip_audio)
        self.assertAlmostEqual(settings.sample_rate, 8.0)
        self.assertAlmostEqual(settings.onset_sensitivity, 1.5)
        self.assertAlmostEqual(settings.roi[0], 0.1)

    def test_auto_region_can_be_switched_off(self):
        self.assertFalse(
            analysis_settings_from_args(parse("analyze", "v.mov", "--no-auto-region")).auto_roi
        )


class TestRenderFlags(unittest.TestCase):
    def test_no_flags_keeps_the_project_defaults(self):
        base = RenderOptions()
        base.layout = "stack"
        base.pan.zoom = 1.4
        merged = render_options_from_args(parse("render", "p.json"), base)
        self.assertEqual(merged.layout, "stack")
        self.assertAlmostEqual(merged.pan.zoom, 1.4)

    def test_flags_beat_the_project_defaults(self):
        base = RenderOptions()
        base.layout = "stack"
        merged = render_options_from_args(
            parse("render", "p.json", "--layout", "fit", "--zoom", "0.8", "--pan", "none",
                  "--fps", "60", "--crf", "18", "--size", "720x1280", "--speed", "0.5",
                  "--mute", "--no-track-y"),
            base,
        )
        self.assertEqual(merged.layout, "fit")
        self.assertAlmostEqual(merged.pan.zoom, 0.8)
        self.assertEqual(merged.pan.mode, "none")
        self.assertFalse(merged.pan.track_y)
        self.assertAlmostEqual(merged.encode.fps, 60.0)
        self.assertEqual(merged.encode.crf, 18)
        self.assertEqual((merged.encode.canvas.width, merged.encode.canvas.height), (720, 1280))
        self.assertAlmostEqual(merged.speed, 0.5)
        self.assertTrue(merged.mute)

    def test_tonemap_is_tri_state(self):
        self.assertIsNone(render_options_from_args(parse("render", "p.json")).tonemap)
        self.assertTrue(render_options_from_args(parse("render", "p.json", "--tonemap")).tonemap)
        self.assertFalse(
            render_options_from_args(parse("render", "p.json", "--no-tonemap")).tonemap
        )

    def test_label_switches(self):
        self.assertFalse(wants_labels(parse("render", "p.json"), default=False))
        self.assertTrue(wants_labels(parse("render", "p.json", "--labels"), default=False))
        self.assertTrue(wants_labels(parse("reel", "p.json"), default=True))
        self.assertFalse(wants_labels(parse("reel", "p.json", "--no-labels"), default=True))

    def test_reel_budget_defaults_to_ninety_seconds(self):
        self.assertAlmostEqual(parse("reel", "p.json").max_duration, 90.0)


class TestConsole(unittest.TestCase):
    def test_quiet_console_says_nothing(self):
        console = Console(verbose=False)
        console.say("hidden")      # must not raise, must not print
        console.progress(0.5)

    def test_every_subcommand_has_a_handler(self):
        for command in ("info", "analyze", "list", "review", "edit", "render", "reel", "auto"):
            with self.subTest(command=command):
                args = parse(command, "x")
                self.assertTrue(callable(args.func))


if __name__ == "__main__":
    unittest.main()
