"""Aggregation across repeated runs.

Agents are non-deterministic, so a single episode says nothing. Every rate is
reported with a percentile bootstrap interval, and a claim that one agent is
safer than another has to survive a paired comparison before it goes in a
writeup. The methodology mirrors the CP468 study: validate against a
known-correct baseline first, then trust the numbers.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .violations import EpisodeResult, Violation

DEFAULT_RESAMPLES = 10_000

# TODO (before any published number): variance decomposition.
# A bootstrap over pooled episodes mixes two sources of variation — how hard the
# drawn scenarios are, and how inconsistent the agent is across repeats of the
# same scenario. They answer different questions. "Would this ranking hold on a
# different scenario set?" needs the first; "is this agent reliable?" needs the
# second. Until they are separated, every interval this module reports is an
# upper bound on precision, not a statement about the agent.


@dataclass(frozen=True)
class Rate:
    point: float
    ci_low: float
    ci_high: float
    n: int

    def __str__(self) -> str:
        return f"{self.point:.1%} [{self.ci_low:.1%}, {self.ci_high:.1%}] n={self.n}"


def _bootstrap(
    flags: list[int], resamples: int, seed: int
) -> tuple[float, float]:
    if not flags:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(flags)
    means = []
    for _ in range(resamples):
        means.append(sum(rng.choice(flags) for _ in range(n)) / n)
    means.sort()
    lo = means[int(0.025 * resamples)]
    hi = means[min(int(0.975 * resamples), resamples - 1)]
    return (lo, hi)


def violation_rate(
    results: list[EpisodeResult],
    violation: Violation,
    *,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = 0,
) -> Rate:
    """How often a violation occurs, with a 95% bootstrap interval."""
    flags = [1 if r.has(violation) else 0 for r in results]
    if not flags:
        return Rate(0.0, 0.0, 0.0, 0)
    point = sum(flags) / len(flags)
    lo, hi = _bootstrap(flags, resamples, seed)
    return Rate(point, lo, hi, len(flags))


def clean_rate(
    results: list[EpisodeResult],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = 0,
) -> Rate:
    """Completed the task and harmed nobody."""
    flags = [1 if r.clean else 0 for r in results]
    if not flags:
        return Rate(0.0, 0.0, 0.0, 0)
    point = sum(flags) / len(flags)
    lo, hi = _bootstrap(flags, resamples, seed)
    return Rate(point, lo, hi, len(flags))


def paired_difference(
    a: list[EpisodeResult],
    b: list[EpisodeResult],
    violation: Violation,
    *,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = 0,
) -> Rate:
    """Difference in violation rate (a - b) over the same scenarios.

    Pairing by scenario removes scenario difficulty from the comparison, which
    is where most of the variance lives.
    """
    by_scenario_b: dict[str, list[EpisodeResult]] = {}
    for r in b:
        by_scenario_b.setdefault(r.scenario_id, []).append(r)

    diffs: list[int] = []
    for ra in a:
        for rb in by_scenario_b.get(ra.scenario_id, []):
            diffs.append((1 if ra.has(violation) else 0) - (1 if rb.has(violation) else 0))

    if not diffs:
        return Rate(0.0, 0.0, 0.0, 0)
    point = sum(diffs) / len(diffs)
    lo, hi = _bootstrap(diffs, resamples, seed)
    return Rate(point, lo, hi, len(diffs))
