"""End-to-end test against a synthetic match clip.

Skipped when ffmpeg is not installed. It builds the fixture from
``make_fixture.py``, so the scripted rally times are known and detection can
be checked against ground truth rather than against itself.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from badminton_reels.ffutil import have_binaries
from badminton_reels.pipeline import AnalyzeSettings, analyze, render_clips, render_reel
from badminton_reels.probe import probe
from badminton_reels.project import Project

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_fixture  # noqa: E402

FIXTURE_DURATION = 28.0


def ffprobe_json(path, *args):
    out = subprocess.run(
        [os.environ.get("BDR_FFPROBE", "ffprobe"), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, check=True,
    ).stdout
    return json.loads(out)


def stream(data, kind):
    return next((s for s in data["streams"] if s["codec_type"] == kind), None)


@unittest.skipUnless(have_binaries(), "ffmpeg and ffprobe are required")
class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.mkdtemp(prefix="bdr-e2e-")
        cls.video = make_fixture.build(
            os.path.join(cls.directory, "match.mp4"), duration=FIXTURE_DURATION
        )
        cls.rallies = make_fixture.rallies_within(FIXTURE_DURATION)
        cls.result = analyze(cls.video, AnalyzeSettings())
        cls.project = cls.result.project
        cls.project.save(os.path.join(cls.directory, "project.json"))

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls.directory, ignore_errors=True)

    def test_detects_every_scripted_rally_and_no_extras(self):
        self.assertEqual(len(self.project.clips), len(self.rallies))
        for (start, end), clip in zip(self.rallies, self.project.clips):
            self.assertAlmostEqual(clip.detect_start, start, delta=1.5)
            self.assertAlmostEqual(clip.detect_end, end, delta=1.5)

    def test_impacts_are_counted_per_rally(self):
        for clip in self.project.clips:
            # Roughly one click every 0.7s in the fixture.
            self.assertGreaterEqual(clip.shots, 3)
            self.assertLessEqual(clip.shots, int(clip.core_duration / 0.4) + 2)

    def test_the_court_region_is_found(self):
        x, y, width, height = self.project.analysis["roi"]
        self.assertGreater(x, 0.05)          # not the whole frame
        self.assertLess(x + width, 0.95)
        self.assertGreater(height, 0.2)

    def test_whole_match_signals_are_stored(self):
        analysis = self.project.analysis
        track, curve = analysis["track"], analysis["curve"]
        self.assertGreater(track["rate"], 0)
        self.assertGreater(len(track["cx"]), 100)
        self.assertEqual(len(track["cx"]), len(track["cy"]))
        self.assertTrue(all(0.0 <= v <= 1.0 for v in track["cx"]))
        # The track should span the file, not just the rallies.
        self.assertAlmostEqual(
            len(track["cx"]) / track["rate"], FIXTURE_DURATION, delta=1.5
        )
        self.assertGreater(len(curve["score"]), 50)
        self.assertGreater(curve["enter"], curve["exit"])
        self.assertGreater(len(analysis["impact_times"]), 10)

    def test_a_clip_track_follows_its_in_point(self):
        clip = self.project.clips[0]
        times, cx, cy = self.project.track_for(clip)
        self.assertGreater(len(times), 10)
        self.assertAlmostEqual(times[0], 0.0, delta=0.2)
        self.assertLessEqual(times[-1], clip.duration + 0.3)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in cx))
        self.assertTrue(all(0.0 <= v <= 1.0 for v in cy))

    def test_project_reloads_and_renders_reels_ready_clips(self):
        reloaded = Project.load(self.project.path)
        out_dir = os.path.join(self.directory, "clips")
        written = render_clips(reloaded, out_dir, clips=reloaded.clips[:1], labels=True)
        self.assertEqual(len(written), 1)
        data = ffprobe_json(written[0])
        video, audio = stream(data, "video"), stream(data, "audio")
        self.assertEqual((video["width"], video["height"]), (1080, 1920))
        self.assertEqual(video["pix_fmt"], "yuv420p")
        self.assertEqual(eval(video["r_frame_rate"]), 30)
        self.assertIsNotNone(audio, "Reels needs an audio track")
        self.assertEqual(audio["channels"], 2)
        self.assertEqual(int(audio["sample_rate"]), 48000)
        self.assertAlmostEqual(
            float(data["format"]["duration"]), reloaded.clips[0].duration, delta=0.4
        )

    def test_every_layout_renders(self):
        for layout in ("follow", "fit", "stack"):
            with self.subTest(layout=layout):
                project = Project.load(self.project.path)
                project.clips[0].layout = layout
                written = render_clips(
                    project, os.path.join(self.directory, "lay-" + layout),
                    clips=project.clips[:1],
                )
                video = stream(ffprobe_json(written[0]), "video")
                self.assertEqual((video["width"], video["height"]), (1080, 1920))

    def test_silent_sources_still_render(self):
        muted = os.path.join(self.directory, "silent.mp4")
        subprocess.run(
            [os.environ.get("BDR_FFMPEG", "ffmpeg"), "-v", "error", "-y", "-i", self.video,
             "-an", "-c:v", "copy", muted],
            check=True,
        )
        info = probe(muted)
        self.assertFalse(info.has_audio)
        result = analyze(muted, AnalyzeSettings())
        self.assertGreaterEqual(len(result.project.clips), 1)
        written = render_clips(
            result.project, os.path.join(self.directory, "silent-out"),
            clips=result.project.clips[:1],
        )
        self.assertIsNotNone(
            stream(ffprobe_json(written[0]), "audio"),
            "a silent source must still get a silent audio track",
        )

    def test_reel_concatenates_the_best_rallies(self):
        project = Project.load(self.project.path)
        out_path = os.path.join(self.directory, "reel.mp4")
        path, chosen = render_reel(project, out_path, max_duration=90.0, labels=True)
        self.assertTrue(os.path.isfile(path))
        expected = sum(clip.duration for clip in chosen)
        self.assertAlmostEqual(
            float(ffprobe_json(path)["format"]["duration"]), expected, delta=0.6
        )
        self.assertEqual([c.start for c in chosen], sorted(c.start for c in chosen))

    def test_reel_respects_a_tight_budget(self):
        project = Project.load(self.project.path)
        path, chosen = render_reel(
            project, os.path.join(self.directory, "short-reel.mp4"), max_duration=11.0
        )
        self.assertLessEqual(sum(clip.duration for clip in chosen), 11.0)
        self.assertGreaterEqual(len(chosen), 1)

    def test_slow_motion_lengthens_the_clip(self):
        from badminton_reels.render import RenderOptions

        project = Project.load(self.project.path)
        options = RenderOptions()
        options.speed = 0.5
        written = render_clips(
            project, os.path.join(self.directory, "slow"),
            options=options, clips=project.clips[:1],
        )
        duration = float(ffprobe_json(written[0])["format"]["duration"])
        self.assertAlmostEqual(duration, project.clips[0].duration / 0.5, delta=0.5)

    def test_cli_runs_the_whole_flow(self):
        out_dir = os.path.join(self.directory, "auto")
        proc = subprocess.run(
            [sys.executable, "-m", "badminton_reels", "auto", self.video,
             "-o", out_dir, "--top", "2"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "project.json")))
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "reel.mp4")))
        self.assertTrue(os.listdir(os.path.join(out_dir, "clips")))


if __name__ == "__main__":
    unittest.main()
