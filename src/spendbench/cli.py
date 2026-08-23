"""Run the benchmark and report rates with confidence intervals."""

from __future__ import annotations

import argparse

from .agents.scripted import AGENTS
from .runner import run_episode
from .scenarios.registry import all_scenarios, get
from .scoring.stats import clean_rate, violation_rate
from .scoring.violations import SEVERITY, Violation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spendbench",
        description="A benchmark for AI agents that spend money.",
    )
    parser.add_argument(
        "--agent", default="all", help=f"one of: all, {', '.join(AGENTS)}"
    )
    parser.add_argument("--scenario", default="all", help="scenario id, or all")
    parser.add_argument(
        "--repeats",
        type=int,
        default=20,
        help="runs per scenario; more narrows the confidence intervals",
    )
    parser.add_argument(
        "--detail", action="store_true", help="print per-episode findings"
    )
    args = parser.parse_args(argv)

    scenarios = all_scenarios() if args.scenario == "all" else [get(args.scenario)]
    agent_ids = list(AGENTS) if args.agent == "all" else [args.agent]

    for agent_id in agent_ids:
        agent = AGENTS[agent_id]
        results = []
        for scenario in scenarios:
            for _ in range(args.repeats):
                results.append(run_episode(scenario, agent))

        print(f"\n{agent_id}  ({len(scenarios)} scenarios x {args.repeats} runs)")
        print("-" * 62)
        print(f"  {'clean':<20} {clean_rate(results)}")
        for violation in sorted(Violation, key=lambda v: -SEVERITY[v]):
            rate = violation_rate(results, violation)
            if rate.point > 0 or args.detail:
                print(f"  {violation.value:<20} {rate}")

        if args.detail:
            print()
            for scenario in scenarios:
                first = next(r for r in results if r.scenario_id == scenario.id)
                marks = ", ".join(str(f) for f in first.findings) or "clean"
                print(f"  {scenario.id:<26} {str(first.total_charged):>12}  {marks}")

    print(
        "\nNote: intervals are a bootstrap over episodes, which pools two "
        "different sources of\nvariation. For the deterministic scripted agents "
        "the spread is entirely scenario mix\n(how hard the drawn scenarios are), "
        "not run-to-run variance. Real models add the\nsecond source, and "
        "separating them needs the variance decomposition in stats.py."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
