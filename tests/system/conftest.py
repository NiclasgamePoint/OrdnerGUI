from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
for source in (
    ROOT / "packages" / "contracts" / "src",
    ROOT / "packages" / "server" / "src",
    ROOT / "packages" / "client" / "src",
):
    value = str(source)
    if value not in sys.path:
        sys.path.insert(0, value)
