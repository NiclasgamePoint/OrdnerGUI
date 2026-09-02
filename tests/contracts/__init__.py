"""Make the independently packaged contracts importable in root test runs."""

from pathlib import Path
import sys


CONTRACTS_SOURCE = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "src"
source = str(CONTRACTS_SOURCE)
if source not in sys.path:
    sys.path.insert(0, source)
