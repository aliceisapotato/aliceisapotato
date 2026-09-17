"""Rally detection: does a scripted activity pattern come back as rallies?"""

import unittest

import numpy as np

from badminton_reels.audio import AudioTrack
from badminton_reels.detect import (
    DetectParams,
    Segment,
    build_activity,
    detect_rallies,
    highlight_score,
    select_for_reel,
)
from badminton_reels.motion import MotionTrack


def motion_track(spans, duration=60.0, rate=12.0, busy=0.05, idle=0.001, cx=0.5):
    """A motion track that is busy inside ``spans`` and nearly still outside."""
    count = int(duration * rate)
    energy = np.full(count, idle)
    centre = np.full(count, cx)
    for start, end in spans:
        lo, hi = int(start * rate), int(end * rate)
        energy[lo:hi] = busy
        centre[lo:hi] = np.linspace(0.25, 0.75, max(hi - lo, 1))
    return MotionTrack(
        sample_rate=rate,
        energy=energy,
        cx=centre,
        cy=np.full(count, 0.5),
        heat=np.zeros((9, 16)),
        grid=(16, 9),
    )


def audio_track(spans, rate=2.0, duration=60.0):
    """Impacts at ``rate`` per second inside each span, none outside."""
    onsets = []
    for start, end in spans:
        onsets.extend(np.arange(start + 0.2, end, 1.0 / rate))
    onsets = np.asarray(sorted(onsets), dtype=np.float64)
    return AudioTrack(
        sample_rate=16000,
        hop_rate=62.5,
        onsets=onsets,
        strength=np.ones(int(duration * 62.5)),
        loudness=np.zeros(int(duration * 62.5)),
        duration=duration,
    )


class TestDetect(unittest.TestCase):
    def test_finds_scripted_rallies(self):
        spans = [(5.0, 15.0), (25.0, 33.0), (42.0, 50.0)]
        segments, _ = detect_rallies(
            60.0, motion_track(spans), audio_track(spans), DetectParams()
        )
        self.assertEqual(len(segments), 3)
        for (start, end), segment in zip(spans, segments):
            # Padding widens each rally, but the core should line up closely.
            self.assertAlmostEqual(segment.detect_start, start, delta=1.0)
            self.assertAlmostEqual(segment.detect_end, end, delta=1.0)
            self.assertLessEqual(segment.start, segment.detect_start)
            self.assertGreaterEqual(segment.end, segment.detect_end)

    def test_padding_never_overlaps_neighbours(self):
        spans = [(5.0, 10.0), (11.6, 17.0)]
        params = DetectParams(merge_gap=0.5, pre_roll=3.0, post_roll=3.0)
        segments, _ = detect_rallies(30.0, motion_track(spans, duration=30.0),
                                     audio_track(spans, duration=30.0), params)
        self.assertEqual(len(segments), 2)
        self.assertLessEqual(segments[0].end, segments[1].start + 1e-6)

    def test_short_bursts_are_dropped(self):
        spans = [(5.0, 6.0), (20.0, 30.0)]
        segments, _ = detect_rallies(
            40.0,
            motion_track(spans, duration=40.0),
            audio_track(spans, duration=40.0),
            DetectParams(min_duration=3.0),
        )
        self.assertEqual(len(segments), 1)
        self.assertAlmostEqual(segments[0].detect_start, 20.0, delta=1.0)

    def test_motion_without_impacts_is_not_a_rally(self):
        """Players strolling about between rallies must not count."""
        spans = [(10.0, 20.0)]
        segments, _ = detect_rallies(
            40.0,
            motion_track(spans, duration=40.0),
            audio_track([], duration=40.0),  # no impacts anywhere
            DetectParams(min_shots=2),
        )
        self.assertEqual(segments, [])

    def test_motion_only_when_there_is_no_audio(self):
        from badminton_reels.audio import empty_track

        spans = [(10.0, 20.0)]
        segments, _ = detect_rallies(
            40.0, motion_track(spans, duration=40.0), empty_track(), DetectParams()
        )
        self.assertEqual(len(segments), 1)

    def test_long_stretch_is_split(self):
        # One long span with a quiet dip in the middle.
        track = motion_track([(5.0, 35.0)], duration=40.0)
        dip = slice(int(19.0 * 12), int(20.0 * 12))
        track.energy[dip] = 0.002
        segments, _ = detect_rallies(
            40.0,
            track,
            audio_track([(5.0, 18.5), (20.5, 35.0)], duration=40.0),
            DetectParams(max_duration=20.0, hang=5.0, merge_gap=5.0),
        )
        self.assertGreaterEqual(len(segments), 2)
        for segment in segments:
            self.assertLessEqual(segment.core_duration, 20.0 + 1e-6)

    def test_thresholds_come_from_the_file(self):
        spans = [(5.0, 15.0)]
        loud = build_activity(30.0, motion_track(spans, duration=30.0, busy=0.4),
                              audio_track(spans, duration=30.0), DetectParams())
        quiet = build_activity(30.0, motion_track(spans, duration=30.0, busy=0.01),
                               audio_track(spans, duration=30.0), DetectParams())
        # Absolute energy differs 40x, yet both files get workable thresholds.
        self.assertGreater(loud.enter_threshold, loud.exit_threshold)
        self.assertGreater(quiet.enter_threshold, quiet.exit_threshold)
        self.assertAlmostEqual(loud.enter_threshold, quiet.enter_threshold, delta=0.25)


class TestRanking(unittest.TestCase):
    def segment(self, start, end, shots, activity=0.6):
        return Segment(start, end, start, end, shots=shots, mean_activity=activity)

    def test_more_shots_ranks_higher(self):
        short_rally = self.segment(0, 8, 4)
        long_rally = self.segment(10, 18, 16)
        self.assertGreater(highlight_score(long_rally), highlight_score(short_rally))

    def test_reel_respects_the_budget_and_stays_chronological(self):
        segments = [
            self.segment(0, 10, 3),
            self.segment(20, 32, 18),
            self.segment(40, 50, 12),
            self.segment(60, 70, 14),
        ]
        for segment in segments:
            segment.score = highlight_score(segment)
        chosen = select_for_reel(segments, max_total=30.0)
        self.assertLessEqual(sum(s.duration for s in chosen), 30.0)
        self.assertEqual([s.start for s in chosen], sorted(s.start for s in chosen))
        # The 18-shot rally is the best one, so it must be in there.
        self.assertIn(20, [s.start for s in chosen])

    def test_reel_respects_the_clip_count(self):
        segments = [self.segment(i * 10, i * 10 + 8, 10) for i in range(6)]
        for segment in segments:
            segment.score = highlight_score(segment)
        self.assertEqual(len(select_for_reel(segments, max_clips=3, max_total=None)), 3)


if __name__ == "__main__":
    unittest.main()
