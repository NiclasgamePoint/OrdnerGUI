from __future__ import annotations

import os


def suppress_windows_crash_dialogs():
    """Keep unattended worker failures from opening blocking OS dialogs."""
    if os.name != "nt":
        return
    try:
        import ctypes

        # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX |
        # SEM_NOOPENFILEERRORBOX
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    except (AttributeError, OSError):
        pass
