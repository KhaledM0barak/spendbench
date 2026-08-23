"""Aggregation across repeated runs.

STUB, but the design is settled and comes from the CP468 study: agents are
non-deterministic, so every scenario runs N times and every reported rate
carries a bootstrap confidence interval. A claim that one agent is safer than
another has to survive the interval before it goes in a writeup.

Planned:
  - violation_rate(results, violation) -> (point estimate, ci_low, ci_high)
  - paired_bootstrap(agent_a, agent_b) over shared scenarios
  - minimum detectable effect at a given N, so the scenario count is chosen
    rather than guessed
"""

from __future__ import annotations

from ..scoring.violations import EpisodeResult, Violation


def violation_rate(
    results: list[EpisodeResult], violation: Violation
) -> tuple[float, float, float]:
    """Point estimate and 95% bootstrap CI for how often a violation occurs."""
    raise NotImplementedError("v1: 10k-resample percentile bootstrap")
