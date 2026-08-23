"""Run the benchmark. STUB."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spendbench")
    parser.add_argument("--agent", help="agent id to benchmark")
    parser.add_argument("--scenario", help="scenario id, or all")
    parser.add_argument("--repeats", type=int, default=20,
                        help="runs per scenario; needed for confidence intervals")
    parser.parse_args(argv)
    raise SystemExit("not implemented: see DESIGN.md")


if __name__ == "__main__":
    main()
