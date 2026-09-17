"""Filtergraph construction. These tests never launch ffmpeg."""

import os
import tempfile
import unittest

from badminton_reels.framing import Canvas, PanParams, plan_frame
from badminton_reels.probe import VideoInfo
from badminton_reels.render import (
    EncodeParams,
    RenderOptions,
    _atempo_chain,
    build_audio_graph,
    build_video_graph,
    encode_args,
    tonemap_chain,
)
from badminton_reels.ffutil import escape_filter_path, escape_filter_value


def info(**kwargs):
    defaults = dict(
        path="/tmp/match.mov", duration=600.0, width=3840, height=2160, fps=59.94,
        codec="hevc", has_audio=True,
    )
    defaults.update(kwargs)
    return VideoInfo(**defaults)


def plan(layout="follow", duration=8.0, **kwargs):
    times = [i * 0.5 for i in range(int(duration * 2) + 1)]
    return plan_frame(
        source_w=3840,
        source_h=2160,
        duration=duration,
        canvas=Canvas(1080, 1920),
        layout=layout,
        sample_times=times,
        cx=[0.3 + 0.4 * (i / len(times)) for i in range(len(times))],
        cy=[0.5] * len(times),
        **kwargs,
    )


class TestVideoGraph(unittest.TestCase):
    def graph(self, plan_obj, source=None, options=None, label=None):
        with tempfile.TemporaryDirectory() as directory:
            statements, warnings = build_video_graph(
                plan_obj,
                source or info(),
                options or RenderOptions(),
                duration=8.0,
                script_dir=directory,
                label=label,
            )
            scripts = sorted(os.listdir(directory))
            contents = []
            for name in scripts:
                with open(os.path.join(directory, name), encoding="utf-8") as handle:
                    contents.append(handle.read())
        return ";".join(statements), warnings, scripts, contents

    def test_follow_uses_one_pass_with_no_overlay(self):
        graph, _, scripts, _ = self.graph(plan("follow"))
        self.assertIn("[0:v]", graph)
        self.assertIn("crop@p0=w=1214:h=2160", graph)
        self.assertIn("scale=1080:1920", graph)
        self.assertNotIn("overlay", graph)
        self.assertNotIn("split", graph)
        self.assertEqual(scripts, ["pan0.cmd"])

    def test_pan_script_is_referenced_and_well_formed(self):
        graph, _, scripts, contents = self.graph(plan("follow"))
        self.assertIn("sendcmd=f=", graph)
        self.assertIn(scripts[0], graph)
        for line in contents[0].splitlines():
            self.assertRegex(line, r"^\d+\.\d{3} crop@p0 [xy] \d+(, crop@p0 [xy] \d+)?;$")

    def test_static_pan_writes_no_script(self):
        graph, _, scripts, _ = self.graph(plan("follow", pan=PanParams(mode="none")))
        self.assertNotIn("sendcmd", graph)
        self.assertEqual(scripts, [])

    def test_fit_blurs_a_background_and_overlays(self):
        graph, _, _, _ = self.graph(plan("fit"))
        self.assertIn("split=2", graph)
        self.assertIn("gblur", graph)
        self.assertIn("overlay=", graph)

    def test_blur_zero_falls_back_to_black_bars(self):
        options = RenderOptions()
        options.blur_sigma = 0.0
        graph, _, _, _ = self.graph(plan("fit"), options=options)
        self.assertNotIn("gblur", graph)
        self.assertIn("color=c=black", graph)
        self.assertIn("overlay=", graph)
        self.assertNotIn("split", graph)  # nothing to branch for

    def test_stack_builds_two_panes(self):
        graph, _, scripts, _ = self.graph(plan("stack"))
        self.assertIn("split=3", graph)
        self.assertIn("crop@p0", graph)
        self.assertIn("crop@p1", graph)
        self.assertEqual(graph.count("overlay="), 2)
        self.assertEqual(scripts, ["pan1.cmd"])  # only the close-up moves

    def test_hdr_sources_are_tone_mapped(self):
        graph, _, _, _ = self.graph(plan(), source=info(color_transfer="arib-std-b67"))
        self.assertTrue("zscale" in graph or "colorspace" in graph)
        self.assertIn("format=yuv420p", graph)

    def test_sdr_sources_are_left_alone(self):
        graph, _, _, _ = self.graph(plan(), source=info(color_transfer="bt709"))
        self.assertNotIn("tonemap", graph)

    def test_tone_mapping_can_be_forced_off(self):
        options = RenderOptions()
        options.tonemap = False
        graph, _, _, _ = self.graph(
            plan(), source=info(color_transfer="arib-std-b67"), options=options
        )
        self.assertNotIn("tonemap", graph)

    def test_speed_and_fades_reach_the_graph(self):
        options = RenderOptions()
        options.speed = 0.5
        options.fade = 0.2
        graph, _, _, _ = self.graph(plan(), options=options)
        self.assertIn("setpts=PTS/0.5", graph)
        self.assertIn("fade=t=in:st=0:d=0.200", graph)
        self.assertIn("fade=t=out:st=7.800", graph)

    def test_a_very_short_clip_gets_no_fade(self):
        with tempfile.TemporaryDirectory() as directory:
            statements, _ = build_video_graph(
                plan(duration=0.2), info(), RenderOptions(),
                duration=0.2, script_dir=directory, label=None,
            )
        self.assertNotIn("fade", ";".join(statements))

    def test_output_frame_rate_is_applied_once(self):
        graph, _, _, _ = self.graph(plan())
        self.assertEqual(graph.count("fps=30"), 1)

    def test_label_is_drawn_or_reported_as_skipped(self):
        graph, warnings, _, _ = self.graph(plan(), label="Rally 3 · 12 shots")
        if "drawtext" in graph:
            self.assertIn("Rally 3", graph)
            self.assertIn("boxcolor", graph)
        else:
            self.assertTrue(any("font" in w for w in warnings))


class TestAudioGraph(unittest.TestCase):
    def test_source_audio_is_kept(self):
        statements = build_audio_graph(info(), RenderOptions(), duration=8.0, music_input=None)
        graph = ";".join(statements)
        self.assertIn("[0:a]", graph)
        self.assertIn("[aout]", graph)
        self.assertNotIn("amix", graph)

    def test_silent_sources_produce_no_graph(self):
        self.assertEqual(
            build_audio_graph(info(has_audio=False), RenderOptions(), duration=8.0,
                              music_input=None),
            [],
        )

    def test_muting_drops_the_source(self):
        options = RenderOptions()
        options.mute = True
        self.assertEqual(
            build_audio_graph(info(), options, duration=8.0, music_input=None), []
        )

    def test_music_is_mixed_under_the_original(self):
        options = RenderOptions()
        options.music = "/tmp/bed.m4a"
        options.music_volume = 0.3
        graph = ";".join(build_audio_graph(info(), options, duration=8.0, music_input=1))
        self.assertIn("[1:a]", graph)
        self.assertIn("volume=0.3", graph)
        self.assertIn("amix=inputs=2", graph)
        self.assertIn("normalize=0", graph)
        self.assertIn("atrim=duration=8.000", graph)

    def test_music_only_when_the_source_is_muted(self):
        options = RenderOptions()
        options.music = "/tmp/bed.m4a"
        options.mute = True
        graph = ";".join(build_audio_graph(info(), options, duration=8.0, music_input=1))
        self.assertNotIn("[0:a]", graph)
        self.assertIn("[1:a]", graph)
        self.assertNotIn("amix", graph)

    def test_speed_changes_retime_the_audio(self):
        options = RenderOptions()
        options.speed = 0.5
        graph = ";".join(build_audio_graph(info(), options, duration=16.0, music_input=None))
        self.assertIn("atempo=0.5", graph)

    def test_atempo_chains_beyond_its_range(self):
        self.assertEqual(_atempo_chain(1.0), "")
        self.assertEqual(_atempo_chain(0.5), "atempo=0.5")
        self.assertEqual(_atempo_chain(0.25), "atempo=0.5,atempo=0.5")
        self.assertIn("atempo=2.0", _atempo_chain(4.0))


class TestEncodeArgs(unittest.TestCase):
    def test_reels_ready_defaults(self):
        args = " ".join(encode_args(RenderOptions()))
        for expected in ("libx264", "yuv420p", "bt709", "+faststart", "aac", "48000"):
            self.assertIn(expected, args)
        self.assertIn("-ac 2", args)

    def test_keyframe_interval_follows_the_frame_rate(self):
        options = RenderOptions(encode=EncodeParams(fps=60.0))
        self.assertIn("120", encode_args(options))

    def test_tonemap_chain_always_ends_in_yuv420p(self):
        chain, _ = tonemap_chain(info(color_transfer="arib-std-b67"))
        self.assertTrue(chain.endswith("format=yuv420p"))


class TestEscaping(unittest.TestCase):
    def test_filter_values_are_escaped(self):
        self.assertEqual(escape_filter_value("Rally 3: 100%"), "Rally 3\\: 100\\%")
        self.assertEqual(escape_filter_value("a,b[c]"), "a\\,b\\[c\\]")

    def test_paths_with_colons_are_escaped(self):
        self.assertEqual(escape_filter_path("C:/fonts/a.ttf"), "C\\:/fonts/a.ttf")


if __name__ == "__main__":
    unittest.main()
