"""Exception types raised by badminton-reels."""


class BdrError(Exception):
    """Base class for every error this package raises deliberately."""


class MissingDependency(BdrError):
    """ffmpeg/ffprobe (or a required filter) is not available."""


class ProbeError(BdrError):
    """The source file could not be inspected."""


class RenderError(BdrError):
    """ffmpeg failed while rendering."""


class ProjectError(BdrError):
    """A project file is missing, malformed, or inconsistent."""
