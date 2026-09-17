"""HDR to SDR conversion.

iPhones record HLG HDR by default. Handing that to an SDR H.264 encoder
without tone mapping is what makes exported clips look washed out and grey,
so both the renderer and the preview proxy run the same conversion.
"""

from __future__ import annotations

from typing import Optional, Tuple

from .ffutil import has_filter, join_filters
from .probe import VideoInfo


def tonemap_chain(info: VideoInfo, npl: float = 100.0) -> Tuple[str, Optional[str]]:
    """Filter chain converting an HDR source to SDR bt709.

    Returns the chain and, when this ffmpeg build cannot do it properly, a
    warning to pass on to the user.
    """
    if has_filter("zscale"):
        transfer = info.color_transfer or "arib-std-b67"
        head = "zscale=tin=%s:t=linear:npl=%g" % (transfer, npl)
        if info.color_primaries:
            head += ":pin=%s" % info.color_primaries
        if info.color_space:
            head += ":min=%s" % info.color_space
        return (
            join_filters([
                head,
                "format=gbrpf32le",
                "tonemap=tonemap=hable:desat=0",
                "zscale=p=bt709:t=bt709:m=bt709:r=tv",
                "format=yuv420p",
            ]),
            None,
        )
    if has_filter("colorspace"):
        return (
            "colorspace=all=bt709:iall=bt2020-10:fast=1,format=yuv420p",
            "this ffmpeg has no zscale filter, so HDR tone mapping is approximate; "
            "colours may look flat. Install a full ffmpeg build for the proper curve.",
        )
    return (
        "format=yuv420p",
        "this ffmpeg can neither tone map nor convert colour spaces; HDR footage "
        "will look washed out. Install a full ffmpeg build, or shoot in SDR.",
    )
