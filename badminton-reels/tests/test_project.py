"""The editable timeline: serialisation, edits, and per-clip overrides."""

import json
import os
import tempfile
import unittest

from badminton_reels.detect import Segment
from badminton_reels.errors import ProjectError
from badminton_reels.pipeline import (
    clip_filename,
    options_for_clip,
    render_options_from_dict,
    render_options_to_dict,
)
from badminton_reels.probe import VideoInfo, format_duration, parse_timecode
from badminton_reels.project import Clip, Project, make_clips, source_dict
from badminton_reels.render import RenderOptions


def info(**kwargs):
    defaults = dict(
        path="/tmp/match.mov", duration=600.0, width=3840, height=2160, fps=59.94,
        codec="hevc", pix_fmt="yuv420p10le", color_transfer="arib-std-b67",
        has_audio=True, audio_sample_rate=48000, audio_channels=2,
    )
    defaults.update(kwargs)
    return VideoInfo(**defaults)


def project(clip_count=3):
    clips = [
        Clip(
            id="r%02d" % i,
            start=10.0 * i,
            end=10.0 * i + 8.0,
            label="Rally %d" % i,
            score=0.1 * i,
            shots=i * 3,
            detect_start=10.0 * i + 0.5,
            detect_end=10.0 * i + 7.0,
        )
        for i in range(1, clip_count + 1)
    ]
    return Project(source=source_dict(info()), clips=clips)


class TestSourceInfo(unittest.TestCase):
    def test_rotation_swaps_the_display_size(self):
        upright = info(rotation=0)
        sideways = info(rotation=-90)
        self.assertEqual((upright.display_width, upright.display_height), (3840, 2160))
        self.assertEqual((sideways.display_width, sideways.display_height), (2160, 3840))
        self.assertTrue(sideways.is_portrait)
        self.assertFalse(upright.is_portrait)

    def test_hlg_and_pq_count_as_hdr(self):
        self.assertTrue(info(color_transfer="arib-std-b67").is_hdr)
        self.assertTrue(info(color_transfer="smpte2084").is_hdr)
        self.assertFalse(info(color_transfer="bt709").is_hdr)
        self.assertFalse(info(color_transfer=None).is_hdr)

    def test_timecodes_round_trip(self):
        self.assertAlmostEqual(parse_timecode("1:23.5"), 83.5)
        self.assertAlmostEqual(parse_timecode("1:02:03"), 3723.0)
        self.assertAlmostEqual(parse_timecode("90s"), 90.0)
        self.assertEqual(format_duration(83.45), "1:23.5")
        self.assertEqual(format_duration(0.0), "0:00.0")


class TestProject(unittest.TestCase):
    def test_save_and_load_round_trip(self):
        original = project()
        original.clips[0].track = [[0.0, 0.4, 0.5], [1.0, 0.6, 0.5]]
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sub", "project.json")
            original.save(path)
            self.assertTrue(os.path.isfile(path))
            loaded = Project.load(path)
        self.assertEqual(len(loaded.clips), len(original.clips))
        self.assertEqual(loaded.clips[0].id, "r01")
        self.assertEqual(loaded.clips[0].track, [[0.0, 0.4, 0.5], [1.0, 0.6, 0.5]])
        self.assertEqual(loaded.source["width"], 3840)

    def test_keep_flags_survive_a_round_trip(self):
        original = project()
        original.clips[1].keep = False
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "p.json")
            original.save(path)
            loaded = Project.load(path)
        self.assertEqual([c.keep for c in loaded.clips], [True, False, True])
        self.assertEqual(len(loaded.kept()), 2)

    def test_unset_overrides_are_left_out_of_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "p.json")
            project(1).save(path)
            with open(path) as handle:
                raw = json.load(handle)
        self.assertNotIn("layout", raw["clips"][0])
        self.assertIn("start", raw["clips"][0])
        self.assertIn("keep", raw["clips"][0])

    def test_missing_file_and_bad_json_are_reported(self):
        with self.assertRaises(ProjectError):
            Project.load("/nonexistent/project.json")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{not json")
        with self.assertRaises(ProjectError):
            Project.load(handle.name)
        os.unlink(handle.name)

    def test_a_newer_version_is_refused(self):
        with self.assertRaises(ProjectError):
            Project.from_dict({"source": {}, "version": 99, "clips": []})

    def test_by_id_explains_itself(self):
        with self.assertRaises(ProjectError) as caught:
            project().by_id("r99")
        self.assertIn("r01", str(caught.exception))

    def test_trim_lengthens_and_shortens(self):
        timeline = project()
        clip = timeline.trim("r01", head=0.5, tail=-1.0)
        self.assertAlmostEqual(clip.start, 9.5)
        self.assertAlmostEqual(clip.end, 17.0)

    def test_trim_cannot_invert_a_clip(self):
        with self.assertRaises(ProjectError):
            project().trim("r01", head=-5.0, tail=-5.0)

    def test_trim_is_clamped_to_the_source(self):
        timeline = project()
        timeline.clips[0].start = 0.2
        clip = timeline.trim("r01", head=5.0, tail=0.0)
        self.assertEqual(clip.start, 0.0)

    def test_renumber_labels_kept_clips_in_order(self):
        timeline = project(4)
        timeline.clips[1].keep = False
        timeline.renumber()
        labels = [(c.id, c.label) for c in timeline.clips]
        self.assertEqual(labels[0], ("r01", "Rally 1"))
        self.assertEqual(labels[1], ("r02", "r02 (dropped)"))
        self.assertEqual(labels[2], ("r03", "Rally 2"))
        self.assertEqual(labels[3], ("r04", "Rally 3"))

    def test_summary_counts_only_kept_footage(self):
        timeline = project(3)
        timeline.clips[0].keep = False
        self.assertAlmostEqual(timeline.total_duration(), 16.0)
        self.assertIn("2 kept", timeline.summary())

    def test_video_info_is_rebuilt_without_ffprobe(self):
        rebuilt = project().video_info()
        self.assertEqual(rebuilt.display_width, 3840)
        self.assertTrue(rebuilt.is_hdr)
        self.assertTrue(rebuilt.has_audio)

    def test_make_clips_numbers_and_labels_rallies(self):
        segments = [
            Segment(2.0, 12.0, 2.5, 11.0, shots=9, score=0.7),
            Segment(20.0, 28.0, 20.5, 27.0, shots=5, score=0.4),
        ]
        clips = make_clips(segments)
        self.assertEqual([c.id for c in clips], ["r01", "r02"])
        self.assertEqual([c.label for c in clips], ["Rally 1", "Rally 2"])
        self.assertEqual(clips[0].shots, 9)
        self.assertAlmostEqual(clips[0].detect_start, 2.5)
        # Tracks live on the project, not the clip.
        self.assertEqual(clips[0].track, [])

    def test_track_is_sliced_out_of_the_whole_match_track(self):
        timeline = project(1)
        # cx ramps 0 -> 1 across 40s at 10 Hz.
        timeline.analysis["track"] = {
            "rate": 10.0,
            "cx": [i / 400.0 for i in range(400)],
            "cy": [0.5] * 400,
        }
        clip = timeline.clips[0]
        clip.set_range(10.0, 20.0)
        times, cx, cy = timeline.track_for(clip)
        self.assertAlmostEqual(times[0], 0.0, delta=0.11)
        self.assertAlmostEqual(times[-1], 10.0, delta=0.21)
        self.assertAlmostEqual(cx[0], 0.25, delta=0.02)
        self.assertAlmostEqual(cx[-1], 0.50, delta=0.02)
        self.assertTrue(all(v == 0.5 for v in cy))

    def test_moving_a_clip_moves_its_track(self):
        """The bug this design avoids: a trimmed clip keeping a stale pan."""
        timeline = project(1)
        timeline.analysis["track"] = {
            "rate": 10.0,
            "cx": [i / 400.0 for i in range(400)],
            "cy": [0.5] * 400,
        }
        clip = timeline.clips[0]
        clip.set_range(5.0, 15.0)
        before = timeline.track_for(clip)[1][0]
        clip.set_range(25.0, 35.0)
        after = timeline.track_for(clip)[1][0]
        self.assertGreater(after, before + 0.4)

    def test_a_per_clip_track_override_wins(self):
        timeline = project(1)
        timeline.analysis["track"] = {"rate": 10.0, "cx": [0.1] * 100, "cy": [0.5] * 100}
        timeline.clips[0].track = [[0.0, 0.9, 0.2], [1.0, 0.8, 0.3]]
        times, cx, cy = timeline.track_for(timeline.clips[0])
        self.assertEqual(cx, [0.9, 0.8])
        self.assertEqual(cy, [0.2, 0.3])

    def test_missing_track_data_is_not_fatal(self):
        times, cx, cy = project(1).track_for(project(1).clips[0])
        self.assertEqual((times, cx, cy), ([], [], []))


class TestClipEditing(unittest.TestCase):
    def timeline(self):
        timeline = project(2)
        timeline.analysis["impact_times"] = [11.0, 12.0, 13.0, 14.0, 15.5, 16.5]
        return timeline

    def test_add_clip_keeps_the_timeline_sorted_with_unique_ids(self):
        timeline = self.timeline()
        added = timeline.add_clip(5.0, 9.0, label="Best point")
        self.assertEqual(added.id, "r03")
        self.assertEqual([c.start for c in timeline.clips], sorted(c.start for c in timeline.clips))
        self.assertEqual(len({c.id for c in timeline.clips}), 3)
        self.assertEqual(added.label, "Best point")

    def test_add_clip_is_clamped_to_the_source(self):
        timeline = self.timeline()
        clip = timeline.add_clip(-5.0, 1e9)
        self.assertEqual(clip.start, 0.0)
        self.assertAlmostEqual(clip.end, timeline.duration)

    def test_add_clip_refuses_an_empty_range(self):
        with self.assertRaises(ProjectError):
            self.timeline().add_clip(10.0, 10.1)

    def test_split_divides_the_span_and_recounts_impacts(self):
        timeline = self.timeline()
        target = timeline.by_id("r02")       # 20.0 - 28.0
        timeline.analysis["impact_times"] = [21.0, 22.0, 26.0]
        head, tail = timeline.split_clip("r02", 24.0)
        self.assertAlmostEqual(head.end, 24.0)
        self.assertAlmostEqual(tail.start, 24.0)
        self.assertAlmostEqual(tail.end, 28.0)
        self.assertEqual(head.shots, 2)
        self.assertEqual(tail.shots, 1)
        self.assertEqual(len(timeline.clips), 3)
        self.assertIs(head, target)

    def test_split_inherits_per_clip_overrides(self):
        timeline = self.timeline()
        timeline.by_id("r02").layout = "stack"
        timeline.by_id("r02").speed = 0.5
        _, tail = timeline.split_clip("r02", 24.0)
        self.assertEqual(tail.layout, "stack")
        self.assertAlmostEqual(tail.speed, 0.5)

    def test_split_too_close_to_an_edge_is_refused(self):
        timeline = self.timeline()
        for at in (20.1, 27.95, 5.0, 100.0):
            with self.subTest(at=at), self.assertRaises(ProjectError):
                timeline.split_clip("r02", at)

    def test_remove_clip(self):
        timeline = self.timeline()
        timeline.remove_clip("r01")
        self.assertEqual([c.id for c in timeline.clips], ["r02"])
        with self.assertRaises(ProjectError):
            timeline.remove_clip("r01")

    def test_count_impacts_in_a_span(self):
        timeline = self.timeline()
        self.assertEqual(timeline.count_impacts(11.0, 14.0), 4)
        self.assertEqual(timeline.count_impacts(0.0, 5.0), 0)

    def test_set_range_refuses_to_invert_a_clip(self):
        clip = self.timeline().clips[0]
        with self.assertRaises(ProjectError):
            clip.set_range(20.0, 19.0)


class TestRenderOptions(unittest.TestCase):
    def test_options_survive_a_dict_round_trip(self):
        options = RenderOptions()
        options.layout = "stack"
        options.pan.zoom = 1.3
        options.speed = 0.5
        restored = render_options_from_dict(render_options_to_dict(options))
        self.assertEqual(restored.layout, "stack")
        self.assertAlmostEqual(restored.pan.zoom, 1.3)
        self.assertAlmostEqual(restored.speed, 0.5)

    def test_clip_overrides_beat_project_defaults(self):
        base = RenderOptions()
        clip = Clip(id="r01", start=0.0, end=5.0, layout="fit", zoom=2.0, speed=0.5, pan="none")
        merged = options_for_clip(clip, base)
        self.assertEqual(merged.layout, "fit")
        self.assertEqual(merged.pan.mode, "none")
        self.assertAlmostEqual(merged.pan.zoom, 2.0)
        self.assertAlmostEqual(merged.speed, 0.5)
        # The project defaults must not be mutated by the merge.
        self.assertEqual(base.layout, "follow")
        self.assertEqual(base.pan.mode, "smooth")
        self.assertAlmostEqual(base.pan.zoom, 1.0)

    def test_filenames_are_ordered_and_safe(self):
        clip = Clip(id="r07", start=0.0, end=1.0, label="Rally 7 / best!")
        self.assertEqual(clip_filename(clip, 3), "03-rally-7-best.mp4")
        self.assertEqual(clip_filename(Clip(id="r01", start=0, end=1, label=""), 1), "01-r01.mp4")


if __name__ == "__main__":
    unittest.main()
