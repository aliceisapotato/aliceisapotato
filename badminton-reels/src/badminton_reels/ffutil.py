"""Thin wrappers around the ffmpeg/ffprobe binaries.

Everything that shells out lives here so the rest of the package can stay
concerned with signal processing and editing decisions.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from typing import Iterable, List, Optional, Sequence

from .errors import MissingDependency, RenderError

FFMPEG = os.environ.get("BDR_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("BDR_FFPROBE", "ffprobe")

# Decoder flags that speed up the analysis pass. Quality loss is irrelevant
# when the frames are about to be scaled down to a ~160px wide grey image.
FAST_DECODE_FLAGS = ["-skip_loop_filter", "all", "-flags2", "+fast"]


def which(binary: str) -> Optional[str]:
    return shutil.which(binary)


def require_binaries() -> None:
    """Raise if ffmpeg or ffprobe is not on PATH."""
    missing = [name for name in (FFMPEG, FFPROBE) if which(name) is None]
    if missing:
        raise MissingDependency(
            "Missing required program(s): %s. Install ffmpeg (macOS: `brew install ffmpeg`, "
            "Debian/Ubuntu: `sudo apt install ffmpeg`) or point BDR_FFMPEG/BDR_FFPROBE at it."
            % ", ".join(missing)
        )


def have_binaries() -> bool:
    return which(FFMPEG) is not None and which(FFPROBE) is not None


@functools.lru_cache(maxsize=1)
def available_filters() -> frozenset:
    """Names of the filters this ffmpeg build was compiled with."""
    try:
        out = subprocess.run(
            [FFMPEG, "-v", "quiet", "-filters"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except OSError:
        return frozenset()
    names = set()
    for line in out.splitlines():
        parts = line.split()
        # Lines look like: " TSC zscale  V->V  Apply resizing..."
        if len(parts) >= 2 and not line.startswith("Filters:"):
            names.add(parts[1])
    return frozenset(names)


def has_filter(name: str) -> bool:
    filters = available_filters()
    # An empty set means we could not ask; assume the filter exists and let
    # ffmpeg complain with a precise message instead of guessing here.
    return not filters or name in filters


def run(
    args: Sequence[str],
    *,
    capture: bool = True,
    check: bool = True,
    stdin_data: Optional[bytes] = None,
) -> subprocess.CompletedProcess:
    """Run a command, raising RenderError with ffmpeg's own message on failure."""
    proc = subprocess.run(
        list(args),
        input=stdin_data,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if check and proc.returncode != 0:
        tail = ""
        if proc.stderr:
            tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            tail = "\n".join(tail[-12:])
        raise RenderError(
            "%s exited with %d\n%s" % (os.path.basename(args[0]), proc.returncode, tail)
        )
    return proc


def ffmpeg_base(*, overwrite: bool = True, quiet: bool = True) -> List[str]:
    """Common leading arguments for an ffmpeg invocation."""
    args = [FFMPEG, "-nostdin", "-hide_banner"]
    args += ["-v", "error"] if quiet else ["-v", "warning", "-stats"]
    args.append("-y" if overwrite else "-n")
    return args


def escape_filter_value(value: str) -> str:
    """Escape a string used as a filter argument value (e.g. drawtext text)."""
    out = []
    for ch in value:
        if ch in "\\':%,[]=;":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def escape_filter_path(path: str) -> str:
    """Escape a filesystem path used inside a filtergraph (sendcmd, fontfile)."""
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def join_filters(steps: Iterable[str]) -> str:
    """Join filter steps with commas, dropping empties."""
    return ",".join(s for s in steps if s)
