"""Audio analysis: find racket-on-shuttle impacts.

A badminton rally is a burst of sharp, broadband "pock" transients roughly
every 0.4-1.5 seconds. Dead time between rallies has footsteps and talking but
almost no transients. Counting impacts is therefore both a better rally
detector than motion alone and a good proxy for rally quality: a 14-shot
rally is a better highlight than a 3-shot service error of the same length.

The detector is a band-limited spectral flux onset detector with an adaptive
threshold, which is the standard recipe for percussive onsets.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .ffutil import FFMPEG
from .probe import VideoInfo

# Racket impacts put most of their energy well above the voice range; limiting
# the flux to this band keeps shouting and crowd noise from firing the detector.
DEFAULT_BAND = (1200.0, 7500.0)

_FRAMES_PER_CHUNK = 2048


@dataclass
class AudioTrack:
    """Impact times plus the envelopes they were derived from."""

    sample_rate: int
    hop_rate: float
    onsets: np.ndarray
    strength: np.ndarray = field(repr=False)
    loudness: np.ndarray = field(repr=False)
    duration: float = 0.0

    @property
    def available(self) -> bool:
        return self.hop_rate > 0 and self.strength.size > 0

    def onsets_between(self, start: float, end: float) -> np.ndarray:
        if self.onsets.size == 0:
            return self.onsets
        lo = np.searchsorted(self.onsets, start, side="left")
        hi = np.searchsorted(self.onsets, end, side="right")
        return self.onsets[lo:hi]

    def count_between(self, start: float, end: float) -> int:
        return int(self.onsets_between(start, end).size)

    def impact_density(self, times: np.ndarray, window: float = 1.0) -> np.ndarray:
        """Impacts per second around each time in ``times``."""
        if self.onsets.size == 0 or times.size == 0:
            return np.zeros_like(times, dtype=np.float64)
        half = window / 2.0
        left = np.searchsorted(self.onsets, times - half, side="left")
        right = np.searchsorted(self.onsets, times + half, side="right")
        return (right - left).astype(np.float64) / float(window)


def empty_track() -> AudioTrack:
    return AudioTrack(
        sample_rate=0,
        hop_rate=0.0,
        onsets=np.zeros(0),
        strength=np.zeros(0),
        loudness=np.zeros(0),
    )


def _decode_mono(info: VideoInfo, sample_rate: int) -> np.ndarray:
    cmd = [
        FFMPEG,
        "-nostdin",
        "-hide_banner",
        "-v",
        "error",
        "-i",
        info.path,
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0 or not proc.stdout:
        return np.zeros(0, dtype=np.float32)
    raw = proc.stdout
    if len(raw) % 2:
        raw = raw[: len(raw) - 1]
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def _moving_stats(values: np.ndarray, width: int) -> Tuple[np.ndarray, np.ndarray]:
    """Centred moving mean and standard deviation with edge padding."""
    width = max(3, int(width) | 1)
    half = width // 2
    padded = np.pad(values, (half, half), mode="edge")
    csum = np.concatenate(([0.0], np.cumsum(padded, dtype=np.float64)))
    csum2 = np.concatenate(([0.0], np.cumsum(padded.astype(np.float64) ** 2)))
    counts = float(width)
    mean = (csum[width:] - csum[:-width]) / counts
    mean_sq = (csum2[width:] - csum2[:-width]) / counts
    var = np.maximum(mean_sq - mean**2, 0.0)
    return mean[: values.size], np.sqrt(var)[: values.size]


def pick_onsets(
    strength: np.ndarray,
    hop_rate: float,
    *,
    loudness: Optional[np.ndarray] = None,
    local_window: float = 0.9,
    local_factor: float = 1.5,
    global_relative: float = 0.15,
    min_gain_db: float = 4.0,
    min_separation: float = 0.09,
) -> np.ndarray:
    """Peak-pick an onset strength curve into impact times (seconds).

    A candidate has to clear three tests, because any one of them alone
    misfires on real footage:

    * the local adaptive threshold, which finds transients in context;
    * a floor tied to the loudest transients in the file, so that a hall with
      no play in it does not turn its own hiss into a rally;
    * a loudness gate, because a racket impact is audibly louder than the
      background it sits in, while noise peaks are not.
    """
    if strength.size == 0 or hop_rate <= 0:
        return np.zeros(0)

    mean, std = _moving_stats(strength, int(round(local_window * hop_rate)))
    median = float(np.median(strength))
    # Anchor on the very top of the distribution: impacts occupy well under
    # 1% of frames, so a lower percentile sits inside the noise.
    high = float(np.percentile(strength, 99.5))
    floor = median + global_relative * max(high - median, 1e-9)
    threshold = np.maximum(mean + local_factor * std, floor)

    candidates = np.flatnonzero(strength > threshold)
    if candidates.size and loudness is not None and loudness.size == strength.size:
        background = float(np.percentile(loudness, 20))
        candidates = candidates[loudness[candidates] >= background + max(min_gain_db, 0.0)]
    if candidates.size == 0:
        return np.zeros(0)

    min_gap = max(1, int(round(min_separation * hop_rate)))
    picked: List[int] = []
    # Walk candidates in descending strength so the loudest hit in a cluster
    # wins, then enforce the minimum spacing between accepted impacts.
    order = candidates[np.argsort(-strength[candidates])]
    taken = np.zeros(strength.size, dtype=bool)
    for idx in order:
        lo, hi = max(0, idx - min_gap), min(strength.size, idx + min_gap + 1)
        if taken[lo:hi].any():
            continue
        taken[idx] = True
        picked.append(int(idx))
    picked.sort()
    return np.asarray(picked, dtype=np.float64) / hop_rate


def analyze_audio(
    info: VideoInfo,
    *,
    sample_rate: int = 16000,
    win: int = 1024,
    hop: int = 256,
    band: Tuple[float, float] = DEFAULT_BAND,
    onset_sensitivity: float = 1.0,
) -> AudioTrack:
    """Detect racket impacts in the source's audio track."""
    if not info.has_audio:
        return empty_track()
    samples = _decode_mono(info, sample_rate)
    if samples.size < win * 2:
        return empty_track()

    hop_rate = sample_rate / float(hop)
    window = np.hanning(win).astype(np.float32)
    freqs = np.fft.rfftfreq(win, 1.0 / sample_rate)
    band_bins = np.flatnonzero((freqs >= band[0]) & (freqs <= band[1]))
    if band_bins.size == 0:
        band_bins = np.arange(len(freqs))

    frames = 1 + (samples.size - win) // hop
    view = np.lib.stride_tricks.sliding_window_view(samples, win)[::hop]
    view = view[:frames]

    strength = np.zeros(frames, dtype=np.float32)
    loudness = np.zeros(frames, dtype=np.float32)
    previous: Optional[np.ndarray] = None

    for start in range(0, frames, _FRAMES_PER_CHUNK):
        stop = min(frames, start + _FRAMES_PER_CHUNK)
        block = view[start:stop] * window
        spectrum = np.abs(np.fft.rfft(block, axis=1)).astype(np.float32)
        log_mag = np.log1p(spectrum * 200.0)
        band_mag = log_mag[:, band_bins]
        if previous is not None:
            band_mag = np.vstack([previous, band_mag])
        flux = np.diff(band_mag, axis=0)
        np.maximum(flux, 0.0, out=flux)
        chunk_strength = flux.sum(axis=1)
        if previous is None:
            # No earlier frame for the very first difference.
            chunk_strength = np.concatenate(([0.0], chunk_strength))
        strength[start:stop] = chunk_strength[: stop - start]
        loudness[start:stop] = 20.0 * np.log10(
            np.sqrt((block.astype(np.float64) ** 2).mean(axis=1)) + 1e-7
        )
        previous = band_mag[-1:]

    sensitivity = max(0.1, float(onset_sensitivity))
    onsets = pick_onsets(
        strength,
        hop_rate,
        loudness=loudness,
        local_factor=1.5 / sensitivity,
        global_relative=0.15 / sensitivity,
        min_gain_db=4.0 / sensitivity,
    )
    return AudioTrack(
        sample_rate=sample_rate,
        hop_rate=hop_rate,
        onsets=onsets,
        strength=strength,
        loudness=loudness,
        duration=samples.size / float(sample_rate),
    )
