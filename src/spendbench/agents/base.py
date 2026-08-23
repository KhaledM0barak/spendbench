"""The interface an agent implements to be benchmarked.

STUB. Kept deliberately thin: anything that can take a task and a store and act
is benchmarkable, including a hand-written baseline. The first agent to build is
a scripted one that behaves correctly, so the harness can be validated before
any model is pointed at it.
"""

from __future__ import annotations

from typing import Protocol

from ..env.store import Store
from ..scenarios.registry import Scenario


class Agent(Protocol):
    id: str

    def run(self, scenario: Scenario, store: Store) -> None:
        """Attempt the task. Raising is a failed episode, not a crash."""
        ...
