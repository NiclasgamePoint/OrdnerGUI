from __future__ import annotations

from pathlib import Path
import os
import sys


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
for source in (
    ROOT / "packages" / "contracts" / "src",
    ROOT / "packages" / "client" / "src",
):
    value = str(source)
    if value not in sys.path:
        sys.path.insert(0, value)
