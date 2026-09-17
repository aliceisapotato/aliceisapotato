"""Turn a 16:9 court into a 9:16 frame without losing the players.

A 9:16 window cut from a 16:9 frame keeps only 32% of the width, and on
0.5x ultra-wide footage the players are already small, so the choice of
framing matters more than anything else in the render:

``follow``
    A 9:16 window the full height of the source, panned to keep the action
    centred. Most detail per player. Best for footage shot from behind the
    baseline, where the rally runs away from the camera rather than across.
``fit``
    The whole court, scaled to the canvas width and floated over a blurred
    copy of itself. Nothing is ever cut off, but players are tiny.
``stack``
    The whole court across the top, a tracked close-up filling the rest.
    Context and detail at once - the best default for side-on footage.

The pan is planned offline, so it is smoothed without the lag a live camera
operator would have: a median filter kills shuttle-sized jitter, a
zero-phase exponential filter removes the remaining wobble, and a speed
limit prevents whip pans.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

LAYOUTS = ("follow", "fit", "stack")
PAN_MODES = ("smooth", "none")


@dataclass(frozen=True)
class Canvas:
    """The output frame. 1080x1920 is the Reels native size."""

    width: int = 1080
    height: int = 1920

    @property
    def aspect(self) -> float:
        return self.width / float(self.height)


@dataclass
class PanParams:
    """How the tracking window is allowed to move."""

    mode: str = "smooth"
    zoom: float = 1.0
    tau: float = 0.7          # seconds; larger is calmer
    median: float = 0.5       # seconds of median filtering
    max_speed: float = 0.6    # crop widths per second
    center_bias: float = 0.15  # pull toward the middle of the frame
    track_y: bool = True

    def validate(self) -> "PanParams":
        if self.mode not in PAN_MODES:
            raise ValueError("pan mode must be one of %s" % (PAN_MODES,))
        if self.zoom <= 0:
            raise ValueError("zoom must be positive")
        return self


@dataclass
class Pane:
    """A source rectangle (possibly moving) drawn onto a canvas rectangle."""

    crop_w: int
    crop_h: int
    dest_x: int
    dest_y: int
    scaled_w: int
    scaled_h: int
    times: List[float] = field(default_factory=list)
    xs: List[int] = field(default_factory=list)
    ys: List[int] = field(default_factory=list)

    @property
    def animated(self) -> bool:
        return len(set(self.xs)) > 1 or len(set(self.ys)) > 1

    @property
    def start_x(self) -> int:
        return self.xs[0] if self.xs else 0

    @property
    def start_y(self) -> int:
        return self.ys[0] if self.ys else 0


@dataclass
class FramePlan:
    """Everything the renderer needs to compose one clip's vertical frame."""

    canvas: Canvas
    panes: List[Pane]
    blur_background: bool = False
    layout: str = "follow"

    @property
    def animated(self) -> bool:
        return any(pane.animated for pane in self.panes)


def even(value: float, multiple: int = 2) -> int:
    """Largest multiple of ``multiple`` that fits in ``value`` (chroma needs 2).

    Sizes round down so a crop can never end up wider than its frame.
    """
    return int(max(multiple, math.floor(float(value) / multiple) * multiple))


def offset(value: float, limit: int) -> int:
    """Round a crop offset to an even pixel inside ``[0, limit]``.

    Unlike a size, zero is a perfectly good offset, and chroma subsampling
    still wants it even.
    """
    snapped = int(round(float(value) / 2.0) * 2)
    return max(0, min(snapped, int(limit) - int(limit) % 2))


def fit_rect(source_w: int, source_h: int, aspect: float, zoom: float = 1.0) -> Tuple[int, int]:
    """Largest ``aspect`` rectangle inside the frame, then scaled by ``zoom``.

    ``zoom`` above 1 tightens the crop; below 1 it widens it past the target
    aspect, which the renderer then letterboxes over a blurred background.
    """
    width = min(float(source_w), source_h * aspect)
    height = width / aspect
    if zoom != 1.0:
        width /= zoom
        height /= zoom
    width = min(width, float(source_w))
    height = min(height, float(source_h))
    return even(width), even(height)


def median_filter(values: np.ndarray, width: int) -> np.ndarray:
    """Odd-width median filter with edge padding."""
    if values.size == 0:
        return values
    width = max(1, int(width) | 1)
    if width == 1:
        return values.astype(np.float64)
    half = width // 2
    padded = np.pad(values.astype(np.float64), (half, half), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, width)
    return np.median(windows, axis=1)


def ema_zero_phase(values: np.ndarray, alpha: float) -> np.ndarray:
    """Exponential smoothing run forwards then backwards, so it adds no lag."""
    if values.size == 0:
        return values
    alpha = min(max(float(alpha), 1e-4), 1.0)
    out = values.astype(np.float64).copy()
    for _ in range(2):
        acc = out[0]
        for i in range(out.size):
            acc += alpha * (out[i] - acc)
            out[i] = acc
        out = out[::-1].copy()
    return out


def limit_speed(values: np.ndarray, max_step: float) -> np.ndarray:
    """Clamp the per-sample change, alternating direction so it stays centred."""
    if values.size < 2 or max_step <= 0:
        return values.astype(np.float64)
    out = values.astype(np.float64).copy()
    for direction in (1, -1):
        indices = range(1, out.size) if direction == 1 else range(out.size - 2, -1, -1)
        for i in indices:
            previous = out[i - direction]
            out[i] = min(max(out[i], previous - max_step), previous + max_step)
    return out


def plan_pan(
    sample_times: Sequence[float],
    centers: Sequence[float],
    *,
    out_times: np.ndarray,
    span: int,
    limit: int,
    pan: PanParams,
    frame_center: float,
) -> np.ndarray:
    """Smooth a sequence of desired centres into a clamped crop offset path."""
    out_times = np.asarray(out_times, dtype=np.float64)
    if out_times.size == 0:
        return np.zeros(0)
    if limit <= 0:
        return np.zeros(out_times.size)

    values = np.asarray(centers, dtype=np.float64)
    times = np.asarray(sample_times, dtype=np.float64)
    if values.size == 0:
        values = np.full(1, frame_center)
        times = np.zeros(1)

    if pan.mode == "none" or values.size < 3:
        fixed = float(np.median(values))
        offsets = np.full(out_times.size, fixed - span / 2.0)
        return np.clip(offsets, 0, limit)

    dt = float(np.median(np.diff(times))) if times.size > 1 else 0.1
    dt = dt if dt > 1e-6 else 0.1

    smooth = median_filter(values, int(round(pan.median / dt)))
    bias = min(max(pan.center_bias, 0.0), 1.0)
    smooth = smooth * (1.0 - bias) + frame_center * bias
    smooth = ema_zero_phase(smooth, 1.0 - math.exp(-dt / max(pan.tau, 1e-3)))

    path = np.interp(out_times, times, smooth)
    out_dt = float(np.median(np.diff(out_times))) if out_times.size > 1 else dt
    path = limit_speed(path, pan.max_speed * span * max(out_dt, 1e-6))

    offsets = path - span / 2.0
    return np.clip(offsets, 0, limit)


def _scaled_size(
    crop_w: int, crop_h: int, dest_w: int, dest_h: int, snap: int = 8
) -> Tuple[int, int]:
    """Fit the crop inside the destination without distorting it.

    A 9:16 window cut from a 16:9 frame cannot land on exactly 1080x1920 once
    both dimensions are rounded to even pixels, so a fit that misses by a few
    pixels is snapped to the destination: half a percent of stretch is
    invisible, whereas a two-pixel black seam is not.
    """
    factor = min(dest_w / float(crop_w), dest_h / float(crop_h))
    width, height = even(crop_w * factor), even(crop_h * factor)
    if 0 <= dest_w - width <= snap and 0 <= dest_h - height <= snap:
        return int(dest_w), int(dest_h)
    return width, height


def _pane(
    *,
    source_w: int,
    source_h: int,
    crop_w: int,
    crop_h: int,
    dest: Tuple[int, int, int, int],
    sample_times: Sequence[float],
    cx: Sequence[float],
    cy: Sequence[float],
    out_times: np.ndarray,
    pan: PanParams,
    track: bool,
) -> Pane:
    dest_x, dest_y, dest_w, dest_h = dest
    scaled_w, scaled_h = _scaled_size(crop_w, crop_h, dest_w, dest_h)
    x_limit, y_limit = source_w - crop_w, source_h - crop_h

    if track:
        xs = plan_pan(
            sample_times,
            np.asarray(cx, dtype=np.float64) * source_w,
            out_times=out_times,
            span=crop_w,
            limit=x_limit,
            pan=pan,
            frame_center=source_w / 2.0,
        )
        if pan.track_y and y_limit > 0:
            ys = plan_pan(
                sample_times,
                np.asarray(cy, dtype=np.float64) * source_h,
                out_times=out_times,
                span=crop_h,
                limit=y_limit,
                pan=pan,
                frame_center=source_h / 2.0,
            )
        else:
            ys = np.full(out_times.size, y_limit / 2.0)
    else:
        xs = np.full(out_times.size, x_limit / 2.0)
        ys = np.full(out_times.size, y_limit / 2.0)

    return Pane(
        crop_w=crop_w,
        crop_h=crop_h,
        dest_x=int(dest_x + (dest_w - scaled_w) // 2),
        dest_y=int(dest_y + (dest_h - scaled_h) // 2),
        scaled_w=scaled_w,
        scaled_h=scaled_h,
        times=[round(float(t), 3) for t in out_times],
        xs=[offset(v, x_limit) for v in xs],
        ys=[offset(v, y_limit) for v in ys],
    )


def plan_frame(
    *,
    source_w: int,
    source_h: int,
    duration: float,
    canvas: Canvas = Canvas(),
    layout: str = "follow",
    pan: Optional[PanParams] = None,
    sample_times: Sequence[float] = (),
    cx: Sequence[float] = (),
    cy: Sequence[float] = (),
    out_fps: float = 30.0,
    command_rate: float = 12.0,
) -> FramePlan:
    """Build the composition plan for one clip.

    ``command_rate`` is how often the crop position may change. The path is
    already smooth, so a modest rate keeps the ffmpeg command script small
    while each step stays well under a pixel of visible movement.
    """
    if layout not in LAYOUTS:
        raise ValueError("layout must be one of %s" % (LAYOUTS,))
    pan = (pan or PanParams()).validate()

    rate = max(1.0, min(command_rate, out_fps))
    count = max(2, int(math.ceil(max(duration, 1.0 / rate) * rate)) + 1)
    out_times = np.arange(count, dtype=np.float64) / rate

    if layout == "fit":
        pane = _pane(
            source_w=source_w,
            source_h=source_h,
            crop_w=even(source_w),
            crop_h=even(source_h),
            dest=(0, 0, canvas.width, canvas.height),
            sample_times=sample_times,
            cx=cx,
            cy=cy,
            out_times=out_times,
            pan=pan,
            track=False,
        )
        return FramePlan(canvas=canvas, panes=[pane], blur_background=True, layout=layout)

    if layout == "stack":
        top_h = even(canvas.width * source_h / float(source_w))
        top_h = min(top_h, even(canvas.height * 0.45))
        bottom_h = even(canvas.height - top_h)
        wide = _pane(
            source_w=source_w,
            source_h=source_h,
            crop_w=even(source_w),
            crop_h=even(source_h),
            dest=(0, 0, canvas.width, top_h),
            sample_times=sample_times,
            cx=cx,
            cy=cy,
            out_times=out_times,
            pan=pan,
            track=False,
        )
        close_w, close_h = fit_rect(
            source_w, source_h, canvas.width / float(bottom_h), pan.zoom
        )
        close = _pane(
            source_w=source_w,
            source_h=source_h,
            crop_w=close_w,
            crop_h=close_h,
            dest=(0, top_h, canvas.width, bottom_h),
            sample_times=sample_times,
            cx=cx,
            cy=cy,
            out_times=out_times,
            pan=pan,
            track=True,
        )
        return FramePlan(canvas=canvas, panes=[wide, close], blur_background=True, layout=layout)

    crop_w, crop_h = fit_rect(source_w, source_h, canvas.aspect, pan.zoom)
    pane = _pane(
        source_w=source_w,
        source_h=source_h,
        crop_w=crop_w,
        crop_h=crop_h,
        dest=(0, 0, canvas.width, canvas.height),
        sample_times=sample_times,
        cx=cx,
        cy=cy,
        out_times=out_times,
        pan=pan,
        track=True,
    )
    fills = pane.scaled_w >= canvas.width and pane.scaled_h >= canvas.height
    return FramePlan(canvas=canvas, panes=[pane], blur_background=not fills, layout=layout)


def sendcmd_script(pane: Pane, label: str = "crop") -> str:
    """A sendcmd script that walks ``pane``'s crop window over time."""
    lines: List[str] = []
    # The crop filter is created at the pane's first position, so a command
    # repeating it would be wasted.
    last: Tuple[Optional[int], Optional[int]] = (pane.start_x, pane.start_y)
    for t, x, y in zip(pane.times, pane.xs, pane.ys):
        commands = []
        if x != last[0]:
            commands.append("%s x %d" % (label, x))
        if y != last[1]:
            commands.append("%s y %d" % (label, y))
        if commands:
            lines.append("%.3f %s;" % (max(0.0, t), ", ".join(commands)))
            last = (x, y)
    return "\n".join(lines) + ("\n" if lines else "")
