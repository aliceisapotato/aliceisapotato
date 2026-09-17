"""Motion analysis: where and how much the frame is moving over time.

One decode pass produces three things:

* ``energy``  -- how much the picture changed between consecutive samples.
  During a rally two players lunge and swing; between rallies they stroll and
  pick up the shuttle, which is a much smaller number.
* ``cx``/``cy`` -- the centre of mass of that change, i.e. roughly where the
  action is. This drives the vertical crop so the players stay in frame.
* ``heat``    -- the same change accumulated over the whole clip. On a static
  tripod shot the hot region is the court, which gives us a free region of
  interest and keeps swaying trees or a busy neighbouring court from dragging
  the crop around.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from .color import tonemap_chain
from .errors import RenderError
from .ffutil import FAST_DECODE_FLAGS, FFMPEG, join_filters
from .probe import VideoInfo

Roi = Tuple[float, float, float, float]  # normalised x, y, w, h
ProgressFn = Optional[Callable[[float], None]]

# Per-frame differences below this (0-255 scale) are treated as sensor and
# codec noise rather than movement.
DEFAULT_NOISE_FLOOR = 6

# The stored maps used for region-of-interest refinement are this many times
# coarser than the decode grid in each axis.
ROI_BLOCK = 4


@dataclass
class MotionTrack:
    """Sampled motion statistics for a whole source file."""

    sample_rate: float
    energy: np.ndarray
    cx: np.ndarray
    cy: np.ndarray
    heat: np.ndarray = field(repr=False)
    grid: Tuple[int, int] = (0, 0)
    roi: Optional[Roi] = None

    @property
    def times(self) -> np.ndarray:
        return np.arange(len(self.energy), dtype=np.float64) / self.sample_rate

    @property
    def duration(self) -> float:
        return len(self.energy) / self.sample_rate if self.sample_rate else 0.0

    def sample_at(self, t: float) -> Tuple[float, float, float]:
        """Linear interpolation of (energy, cx, cy) at time ``t``."""
        if len(self.energy) == 0:
            return 0.0, 0.5, 0.5
        pos = np.clip(t * self.sample_rate, 0, len(self.energy) - 1)
        lo = int(np.floor(pos))
        hi = min(lo + 1, len(self.energy) - 1)
        frac = pos - lo
        blend = lambda a: float(a[lo] * (1 - frac) + a[hi] * frac)  # noqa: E731
        return blend(self.energy), blend(self.cx), blend(self.cy)

    def window(self, start: float, end: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Samples (times, cx, cy) inside ``[start, end]``."""
        if len(self.energy) == 0:
            return np.zeros(0), np.zeros(0), np.zeros(0)
        i0 = max(0, int(np.floor(start * self.sample_rate)))
        i1 = min(len(self.energy), int(np.ceil(end * self.sample_rate)) + 1)
        if i1 <= i0:
            i1 = min(len(self.energy), i0 + 1)
        idx = np.arange(i0, i1)
        return idx / self.sample_rate, self.cx[idx], self.cy[idx]


@dataclass
class ProxyRequest:
    """A low-resolution H.264 copy for scrubbing in a browser.

    It is produced by the analysis decode rather than a pass of its own: the
    source is already being decoded, so the proxy costs only its encode.
    """

    path: str
    height: int = 720
    fps: float = 30.0
    crf: int = 26
    tonemap: Optional[bool] = None  # None means "decide from the source"

    def wants_tonemap(self, info: VideoInfo) -> bool:
        return info.is_hdr if self.tonemap is None else bool(self.tonemap)


def _even(value: float, multiple: int = 2) -> int:
    return int(max(multiple, round(value / multiple) * multiple))


def _read_exact(stream, size: int) -> bytes:
    """Read exactly ``size`` bytes unless the stream ends."""
    chunks: List[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _block_mean(frame: np.ndarray, block: int) -> np.ndarray:
    h, w = frame.shape
    return frame.reshape(h // block, block, w // block, block).mean(axis=(1, 3))


def estimate_roi(
    heat: np.ndarray,
    *,
    trim: float = 0.02,
    margin: float = 0.06,
    min_size: float = 0.25,
) -> Roi:
    """Bounding box of the busy part of the frame, in normalised coordinates.

    ``trim`` discards that fraction of the accumulated motion from each edge,
    so a single moving object outside the court (a passer-by, a tree) cannot
    stretch the box across the whole frame. ``margin`` then pads the result
    because players reach outside the area their feet cover.
    """
    if heat.size == 0 or not np.isfinite(heat).any() or heat.max() <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    h, w = heat.shape

    def span(mass: np.ndarray, count: int) -> Tuple[float, float]:
        total = mass.sum()
        if total <= 0:
            return 0.0, 1.0
        cdf = np.cumsum(mass) / total
        lo = float(np.searchsorted(cdf, trim) / count)
        hi = float((np.searchsorted(cdf, 1.0 - trim) + 1) / count)
        return lo, min(hi, 1.0)

    x0, x1 = span(heat.sum(axis=0), w)
    y0, y1 = span(heat.sum(axis=1), h)
    x0, x1 = max(0.0, x0 - margin), min(1.0, x1 + margin)
    y0, y1 = max(0.0, y0 - margin), min(1.0, y1 + margin)

    # Never let the estimate collapse onto a tiny patch.
    def grow(lo: float, hi: float) -> Tuple[float, float]:
        if hi - lo >= min_size:
            return lo, hi
        centre = (lo + hi) / 2.0
        lo = max(0.0, centre - min_size / 2.0)
        return lo, min(1.0, lo + min_size)

    x0, x1 = grow(x0, x1)
    y0, y1 = grow(y0, y1)
    return (x0, y0, x1 - x0, y1 - y0)


def _roi_mask(roi: Optional[Roi], shape: Tuple[int, int]) -> Optional[np.ndarray]:
    if roi is None:
        return None
    x, y, w, h = roi
    if (x, y, w, h) == (0.0, 0.0, 1.0, 1.0):
        return None
    rows, cols = shape
    ys = (np.arange(rows) + 0.5) / rows
    xs = (np.arange(cols) + 0.5) / cols
    mask_y = (ys >= y) & (ys <= y + h)
    mask_x = (xs >= x) & (xs <= x + w)
    mask = np.outer(mask_y, mask_x)
    return mask if mask.any() else None


def _stats_from_maps(
    maps: np.ndarray, roi: Optional[Roi]
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Energy and centroid per frame from stacked difference maps."""
    if maps.size == 0:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    frames, rows, cols = maps.shape
    mask = _roi_mask(roi, (rows, cols))
    data = maps.astype(np.float32)
    if mask is not None:
        data = data * mask
        active = float(mask.sum())
    else:
        active = float(rows * cols)

    totals = data.sum(axis=(1, 2))
    energy = totals / (active * 255.0)
    xs = (np.arange(cols, dtype=np.float32) + 0.5) / cols
    ys = (np.arange(rows, dtype=np.float32) + 0.5) / rows
    col_mass = data.sum(axis=1)
    row_mass = data.sum(axis=2)
    safe = np.where(totals > 0, totals, 1.0)
    cx = (col_mass * xs).sum(axis=1) / safe
    cy = (row_mass * ys).sum(axis=1) / safe
    # Frames with no measurable movement inherit the previous position so the
    # crop holds still instead of snapping to the centre of the frame.
    idle = totals <= 0
    cx[idle] = np.nan
    cy[idle] = np.nan
    cx, cy = _forward_fill(cx, 0.5), _forward_fill(cy, 0.5)
    return energy.astype(np.float64), cx, cy


def _forward_fill(values: np.ndarray, default: float) -> np.ndarray:
    out = values.astype(np.float64).copy()
    if out.size == 0:
        return out
    if np.isnan(out[0]):
        first = np.flatnonzero(~np.isnan(out))
        out[0] = out[first[0]] if first.size else default
    for i in range(1, out.size):
        if np.isnan(out[i]):
            out[i] = out[i - 1]
    return out


def analyze_motion(
    info: VideoInfo,
    *,
    sample_rate: float = 12.0,
    analysis_width: int = 192,
    noise_floor: int = DEFAULT_NOISE_FLOOR,
    roi: Optional[Roi] = None,
    auto_roi: bool = True,
    memory_budget_mb: float = 384.0,
    hwaccel: Optional[str] = None,
    proxy: Optional[ProxyRequest] = None,
    progress: ProgressFn = None,
) -> MotionTrack:
    """Decode ``info`` once at low resolution and measure movement over time.

    When ``proxy`` is given, the same decode also writes a small H.264 copy
    for the editor to scrub, tone mapped if the source is HDR.
    """
    fine_w = _even(max(64, analysis_width), ROI_BLOCK * 2)
    fine_h = _even(max(36, fine_w / max(info.aspect, 0.1)), ROI_BLOCK * 2)
    coarse_w, coarse_h = fine_w // ROI_BLOCK, fine_h // ROI_BLOCK

    grey = join_filters([
        "fps=%.6g" % sample_rate,
        "scale=%d:%d:flags=bilinear" % (fine_w, fine_h),
        "format=gray",
    ])
    cmd = [FFMPEG, "-nostdin", "-hide_banner", "-v", "error", "-y"]
    cmd += FAST_DECODE_FLAGS
    if hwaccel:
        cmd += ["-hwaccel", hwaccel]
    cmd += ["-i", info.path]

    if proxy is None:
        cmd += ["-an", "-sn", "-dn", "-vf", grey]
        cmd += ["-f", "rawvideo", "-pix_fmt", "gray", "-"]
    else:
        preview = []
        if proxy.wants_tonemap(info):
            preview.append(tonemap_chain(info)[0])
        preview += [
            "fps=%.6g" % proxy.fps,
            "scale=-2:%d:flags=bilinear" % proxy.height,
            "setsar=1",
        ]
        cmd += [
            "-filter_complex",
            "[0:v]split=2[analyse][preview];[analyse]%s[grey];[preview]%s[proxy]"
            % (grey, join_filters(preview)),
            "-map", "[grey]", "-f", "rawvideo", "-pix_fmt", "gray", "-",
            "-map", "[proxy]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(proxy.crf),
            "-pix_fmt", "yuv420p", "-g", "%d" % max(2, int(proxy.fps)),
            "-c:a", "aac", "-b:a", "96k", "-ac", "2",
            "-movflags", "+faststart", proxy.path,
        ]

    expected = max(1, int(info.duration * sample_rate))
    per_frame_bytes = coarse_w * coarse_h
    store_maps = auto_roi or roi is not None
    if store_maps and expected * per_frame_bytes > memory_budget_mb * 1024 * 1024:
        store_maps = False

    heat = np.zeros((fine_h, fine_w), dtype=np.float64)
    maps: List[np.ndarray] = []
    full_energy: List[float] = []
    full_cx: List[float] = []
    full_cy: List[float] = []

    xs = (np.arange(fine_w, dtype=np.float32) + 0.5) / fine_w
    ys = (np.arange(fine_h, dtype=np.float32) + 0.5) / fine_h
    frame_bytes = fine_w * fine_h
    previous: Optional[np.ndarray] = None
    count = 0

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while True:
            raw = _read_exact(proc.stdout, frame_bytes)
            if len(raw) < frame_bytes:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(fine_h, fine_w)
            if previous is None:
                diff = np.zeros((fine_h, fine_w), dtype=np.float32)
            else:
                diff = np.abs(frame.astype(np.int16) - previous).astype(np.float32)
                diff[diff < noise_floor] = 0.0
            previous = frame.astype(np.int16)

            heat += diff
            total = float(diff.sum())
            full_energy.append(total / (frame_bytes * 255.0))
            if total > 0:
                full_cx.append(float((diff.sum(axis=0) * xs).sum() / total))
                full_cy.append(float((diff.sum(axis=1) * ys).sum() / total))
            else:
                full_cx.append(np.nan)
                full_cy.append(np.nan)
            if store_maps:
                maps.append(_block_mean(diff, ROI_BLOCK).astype(np.uint8))

            count += 1
            if progress is not None and count % 64 == 0:
                progress(min(0.99, count / float(expected)))
    finally:
        if proc.stdout:
            proc.stdout.close()
        stderr = proc.stderr.read() if proc.stderr else b""
        if proc.stderr:
            proc.stderr.close()
        proc.wait()

    if count == 0:
        raise RenderError(
            "no frames decoded from %s\n%s"
            % (info.path, stderr.decode("utf-8", "replace").strip())
        )
    if progress is not None:
        progress(1.0)

    if roi is None and auto_roi:
        roi = estimate_roi(heat)

    if store_maps and maps:
        stacked = np.stack(maps)
        energy, cx, cy = _stats_from_maps(stacked, roi)
    else:
        # Without the stored maps the statistics stay whole-frame; the region
        # of interest is still reported so the crop can use it.
        energy = np.asarray(full_energy, dtype=np.float64)
        cx = _forward_fill(np.asarray(full_cx, dtype=np.float64), 0.5)
        cy = _forward_fill(np.asarray(full_cy, dtype=np.float64), 0.5)

    return MotionTrack(
        sample_rate=sample_rate,
        energy=energy,
        cx=cx,
        cy=cy,
        heat=heat,
        grid=(coarse_w, coarse_h),
        roi=roi,
    )


def parse_roi(text: str) -> Roi:
    """Parse ``x,y,w,h`` given either as fractions (0-1) or percentages."""
    parts = [p.strip() for p in str(text).replace(";", ",").split(",")]
    if len(parts) != 4:
        raise ValueError("region must be x,y,w,h (got %r)" % text)
    values: Sequence[float] = [float(p.rstrip("%")) for p in parts]
    if any(p.endswith("%") for p in parts) or max(values) > 1.0:
        values = [v / 100.0 for v in values]
    x, y, w, h = values
    if w <= 0 or h <= 0:
        raise ValueError("region width and height must be positive")
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    return (x, y, min(w, 1.0 - x), min(h, 1.0 - y))
