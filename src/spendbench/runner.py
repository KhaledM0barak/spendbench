"""Build an environment from a scenario, run an agent in it, and score it."""

from __future__ import annotations

from .env.catalog import Catalog, office_supplies
from .env.faults import Fault
from .env.store import Store
from .scenarios.registry import Scenario
from .scoring.scorer import score
from .scoring.violations import EpisodeResult


def build_store(scenario: Scenario) -> Store:
    catalog: Catalog = office_supplies()

    # An adversarial listing is a catalog mutation, not a checkout-time event:
    # the text has to be there for the agent to read while browsing.
    for spec in scenario.faults:
        if spec.fault is Fault.ADVERSARIAL_LISTING:
            victim = min(
                (p for p in catalog.all() if p.in_stock),
                key=lambda p: p.price.cents,
            )
            from dataclasses import replace

            poisoned = replace(
                victim, description=(victim.description or "") + spec.payload
            )
            catalog = Catalog(
                [poisoned if p.sku == victim.sku else p for p in catalog.all()]
            )

    checkout_faults = tuple(
        s for s in scenario.faults if s.fault is not Fault.ADVERSARIAL_LISTING
    )
    return Store(catalog, faults=checkout_faults)


def run_episode(scenario: Scenario, agent) -> EpisodeResult:
    """One agent, one scenario, one score.

    An agent that raises has simply failed the episode; the exception is not
    allowed to abort the benchmark, and any money it moved before dying is
    still scored.
    """
    store = build_store(scenario)
    try:
        agent.run(scenario, store)
    except Exception:
        pass
    return score(scenario, store, agent.id)
