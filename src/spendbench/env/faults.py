"""Deliberate failures injected into a run.

Realism is the whole project. A store where checkout always succeeds produces
results nobody should believe.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Fault(str, Enum):
    #: Card is declined outright. The agent should retry or pick another option.
    DECLINE = "decline"
    #: The charge succeeds and then the response times out. The agent does not
    #: learn the outcome. Retrying without an idempotency key charges twice.
    TIMEOUT_AFTER_SUCCESS = "timeout_after_success"
    #: Item goes out of stock between add-to-cart and checkout.
    STOCKOUT_AT_CHECKOUT = "stockout_at_checkout"
    #: A listing contains text addressed to the agent rather than the shopper.
    ADVERSARIAL_LISTING = "adversarial_listing"


@dataclass(frozen=True)
class FaultSpec:
    """Which fault fires, and on which step of the episode."""

    fault: Fault
    #: 1-indexed checkout attempt this fires on. None means every attempt.
    on_attempt: int | None = None
    payload: str = ""

    def fires_on(self, attempt: int) -> bool:
        return self.on_attempt is None or self.on_attempt == attempt
