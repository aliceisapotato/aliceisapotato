"""Generate a synthetic 'badminton match' clip for testing.

Landscape frames, two players that dart about during rallies and stroll
between them, and a soundtrack of racket-like clicks during rallies only.
It is not pretty, but it exercises every part of the pipeline, and it lets
you check an install without uploading real footage.

    python tests/make_fixture.py /tmp/match.mp4
    python tests/make_fixture.py /tmp/short.mp4 --duration 25
"""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import tempfile
import wave
from typing import Sequence, Tuple

import numpy as np

Rally = Tuple[float, float]

# (start, end) of each scripted rally, in seconds.
RALLIES: Sequence[Rally] = ((3.0, 11.0), (16.0, 26.0), (31.0, 36.5), (40.0, 46.0))
DURATION = 49.0
WIDTH, HEIGHT = 640, 360
FPS = 30
AUDIO_RATE = 44100


def rallies_within(duration: float, rallies: Sequence[Rally] = RALLIES) -> Sequence[Rally]:
    """The scripted rallies that fit inside ``duration``."""
    return tuple((start, end) for start, end in rallies if end <= duration - 1.0)


def in_rally(t: float, rallies: Sequence[Rally]) -> bool:
    return any(start <= t <= end for start, end in rallies)


def _court(width: int, height: int) -> np.ndarray:
    """A green floor with white lines: static detail for the detector to ignore."""
    court = np.zeros((height, width, 3), dtype=np.uint8)
    court[:, :] = (28, 46, 30)
    x0, x1 = int(width * 0.1875), int(width * 0.8125)
    y0, y1 = int(height * 0.25), int(height * 0.8333)
    court[y0:y1, x0:x1] = (46, 92, 58)
    mid_y = (y0 + y1) // 2
    court[mid_y : mid_y + 3, x0:x1] = (210, 215, 205)
    mid_x = (x0 + x1) // 2
    court[y0:y1, mid_x : mid_x + 3] = (210, 215, 205)
    return court


def _draw(frame: np.ndarray, cx: float, cy: float, size: int, colour) -> None:
    x0 = max(0, int(cx - size // 2))
    y0 = max(0, int(cy - size // 2))
    frame[y0 : y0 + size, x0 : x0 + size] = colour


def frame_at(t: float, court: np.ndarray, rallies: Sequence[Rally]) -> np.ndarray:
    frame = court.copy()
    height, width = court.shape[:2]
    mid_x, mid_y = width / 2.0, height / 2.0
    if in_rally(t, rallies):
        near = (mid_x + 0.234 * width * math.sin(t * 3.1), height * 0.69 + 28 * math.sin(t * 5.3))
        far = (mid_x - 0.203 * width * math.sin(t * 2.7 + 1.0), height * 0.39 + 18 * math.cos(t * 4.1))
        _draw(frame, mid_x + 0.266 * width * math.sin(t * 6.0),
              mid_y - 60 * abs(math.sin(t * 6.0)), 5, (245, 245, 240))
    else:
        near = (mid_x + 40 * math.sin(t * 0.35), height * 0.708 + 6 * math.sin(t * 0.3))
        far = (mid_x - 35 * math.sin(t * 0.3), height * 0.403 + 5 * math.cos(t * 0.25))
    _draw(frame, near[0], near[1], 34, (40, 70, 200))
    _draw(frame, far[0], far[1], 28, (200, 80, 60))
    return frame


def make_audio(duration: float, rallies: Sequence[Rally], seed: int = 7) -> np.ndarray:
    """Room noise plus a click every 0.45-0.95s inside each rally."""
    rng = np.random.default_rng(seed)
    total = int(duration * AUDIO_RATE)
    audio = rng.normal(0.0, 0.002, total).astype(np.float32)
    length = int(0.012 * AUDIO_RATE)
    envelope = np.exp(-np.linspace(0, 9, length))
    tone = np.sin(np.linspace(0, 2 * math.pi * 2600 * 0.012, length))
    for start, end in rallies:
        t = start + 0.35
        while t < end - 0.2:
            at = int(t * AUDIO_RATE)
            click = (0.55 * envelope * (tone + 0.6 * rng.normal(0, 1, length))).astype(np.float32)
            room = max(0, min(length, total - at))
            audio[at : at + room] += click[:room]
            t += float(rng.uniform(0.45, 0.95))
    return np.clip(audio, -1.0, 1.0)


def write_wav(path: str, audio: np.ndarray) -> None:
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(AUDIO_RATE)
        handle.writeframes((audio * 32767).astype("<i2").tobytes())


def build(
    out_path: str,
    *,
    duration: float = DURATION,
    width: int = WIDTH,
    height: int = HEIGHT,
    fps: int = FPS,
    rallies: Sequence[Rally] = None,
) -> str:
    """Write the fixture to ``out_path`` and return the path."""
    rallies = rallies_within(duration) if rallies is None else rallies
    work = tempfile.mkdtemp(prefix="bdr-fixture-")
    wav_path = os.path.join(work, "audio.wav")
    write_wav(wav_path, make_audio(duration, rallies))
    cmd = [
        os.environ.get("BDR_FFMPEG", "ffmpeg"), "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (width, height),
        "-r", str(fps), "-i", "-",
        "-i", wav_path,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", out_path,
    ]
    court = _court(width, height)
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        # Stream frame by frame; buffering a whole clip costs a gigabyte.
        for index in range(int(duration * fps)):
            process.stdin.write(frame_at(index / float(fps), court, rallies).tobytes())
        process.stdin.close()
    except BrokenPipeError:  # pragma: no cover - ffmpeg died early
        pass
    if process.wait() != 0:
        raise SystemExit("ffmpeg failed to build the fixture")
    os.unlink(wav_path)
    os.rmdir(work)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", default="match-fixture.mp4")
    parser.add_argument("--duration", type=float, default=DURATION)
    parser.add_argument("--width", type=int, default=WIDTH)
    parser.add_argument("--height", type=int, default=HEIGHT)
    parser.add_argument("--fps", type=int, default=FPS)
    args = parser.parse_args()
    rallies = rallies_within(args.duration)
    build(args.output, duration=args.duration, width=args.width, height=args.height,
          fps=args.fps, rallies=rallies)
    print(args.output)
    print("scripted rallies: %s" % ", ".join("%.1f-%.1f" % r for r in rallies))
    return 0


if __name__ == "__main__":
    sys.exit(main())
