"""Windows discovery and coordinate contracts."""

from uga.windows.backend import Win32WindowBackend, WindowBackend
from uga.windows.window_identity import WindowIdentity

__all__ = ["WindowBackend", "Win32WindowBackend", "WindowIdentity"]
