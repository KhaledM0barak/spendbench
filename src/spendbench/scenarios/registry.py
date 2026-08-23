"""Scenario definitions: task, budget, acceptance set, armed faults.

STUB. v1 target is ~10 scenarios. Each needs a known-correct outcome so the
scorer has ground truth rather than a judgement call.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..env.faults import FaultSpec
from ..env.money import Money


@dataclass(frozen=True)
class Scenario:
    id: str
    #: Natural-language task handed to the agent.
    task: str
    budget: Money
    #: SKUs that satisfy the task. Anything else is WRONG_ITEM.
    acceptable_skus: frozenset[str]
    faults: tuple[FaultSpec, ...] = ()
    #: True when the correct behaviour is to stop and ask rather than to buy.
    requires_stop: bool = False


REGISTRY: dict[str, Scenario] = {}


def register(scenario: Scenario) -> Scenario:
    if scenario.id in REGISTRY:
        raise ValueError(f"duplicate scenario id: {scenario.id}")
    REGISTRY[scenario.id] = scenario
    return scenario
