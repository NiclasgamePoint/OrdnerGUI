"""Profile the synthetic recognition benchmark without opening customer data."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "profiles/recognition.html")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")

    try:
        from pyinstrument import Profiler
    except ImportError:
        parser.error("Install development tools: python -m pip install -r requirements-dev.txt")
    from tools.recognition_benchmark import run_benchmark

    profiler = Profiler()
    profiler.start()
    try:
        for _ in range(args.repeat):
            run_benchmark()
    finally:
        profiler.stop()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        profiler.write_html(str(args.output))
    print(f"Synthetic recognition profile: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
