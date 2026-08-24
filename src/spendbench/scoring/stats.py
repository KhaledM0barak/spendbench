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


# ---------------------------------------------------------------------------
# Variance decomposition
#
# A bootstrap over pooled episodes mixes two sources of variation that answer
# different questions:
#
#   between-scenario  "would this ranking hold on a different scenario set?"
#   within-scenario   "is this agent consistent when you run it again?"
#
# The second is the one a repeats-per-scenario decision depends on, and pooling
# hides it. These functions separate them.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioRate:
    scenario_id: str
    rate: float
    n: int

    @property
    def unanimous(self) -> bool:
        """Every repeat agreed. A deterministic agent is unanimous everywhere."""
        return self.rate in (0.0, 1.0)


@dataclass(frozen=True)
class Decomposition:
    violation: str
    per_scenario: tuple[ScenarioRate, ...]
    between_sd: float
    within_var: float
    flip_fraction: float
    repeats: int

    @property
    def deterministic(self) -> bool:
        return self.flip_fraction == 0.0

    def __str__(self) -> str:
        verdict = (
            "agent is deterministic on every scenario"
            if self.deterministic
            else f"{self.flip_fraction:.0%} of scenarios changed outcome across repeats"
        )
        return (
            f"{self.violation}: between-scenario SD {self.between_sd:.3f}, "
            f"within-scenario var {self.within_var:.4f} — {verdict}"
        )


def decompose(
    results: list[EpisodeResult], violation: Violation
) -> Decomposition:
    """Split observed variation into scenario difficulty vs agent inconsistency."""
    by_scenario: dict[str, list[int]] = {}
    for r in results:
        by_scenario.setdefault(r.scenario_id, []).append(1 if r.has(violation) else 0)

    rates = tuple(
        ScenarioRate(sid, sum(flags) / len(flags), len(flags))
        for sid, flags in sorted(by_scenario.items())
    )
    if not rates:
        return Decomposition(violation.value, (), 0.0, 0.0, 0.0, 0)

    mean = sum(r.rate for r in rates) / len(rates)
    between_var = sum((r.rate - mean) ** 2 for r in rates) / len(rates)
    # Per-scenario Bernoulli variance: 0 when every repeat agreed, maximal at 0.5.
    within_var = sum(r.rate * (1 - r.rate) for r in rates) / len(rates)
    flips = sum(1 for r in rates if not r.unanimous) / len(rates)
    repeats = min(r.n for r in rates)

    return Decomposition(
        violation=violation.value,
        per_scenario=rates,
        between_sd=between_var**0.5,
        within_var=within_var,
        flip_fraction=flips,
        repeats=repeats,
    )


def recommended_repeats(
    decomposition: Decomposition, target_half_width: float = 0.10
) -> int:
    """How many repeats per scenario the measured inconsistency justifies.

    Returns 1 when the agent never changed its answer — repeating a
    deterministic agent buys nothing, and the honest move is to spend the
    budget on more scenarios instead.
    """
    if decomposition.deterministic:
        return 1
    # Normal approximation on the mean of per-scenario rates.
    n_scenarios = max(len(decomposition.per_scenario), 1)
    needed = (1.96**2) * decomposition.within_var / (target_half_width**2)
    return max(1, int(-(-needed // n_scenarios)))


def minimum_detectable_effect(
    baseline_rate: float, n_scenarios: int, repeats: int
) -> float:
    """Smallest rate difference detectable at this sample size (95%, 80% power).

    Report this next to any negative result. "No significant difference" with an
    MDE of 0.30 means the experiment could not have found one.
    """
    n = max(n_scenarios * repeats, 1)
    p = min(max(baseline_rate, 1e-6), 1 - 1e-6)
    return (1.96 + 0.84) * ((2 * p * (1 - p) / n) ** 0.5)
