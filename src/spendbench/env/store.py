"""The surface an agent talks to: search, view, cart, checkout.

STUB. Deliberately small and boring — the interesting behaviour lives in
payments.py and faults.py. Checkout is the only method that can produce a
charge, and it must route through PaymentProcessor so the ledger sees every
attempt.
"""

from __future__ import annotations

from .catalog import Catalog
from .faults import FaultSpec
from .payments import PaymentProcessor


class Store:
    def __init__(self, catalog: Catalog, faults: list[FaultSpec] | None = None) -> None:
        self.catalog = catalog
        self.faults = faults or []
        self.payments = PaymentProcessor()
        self._attempt = 0

    def search(self, query: str):
        raise NotImplementedError

    def view(self, sku: str):
        raise NotImplementedError

    def add_to_cart(self, sku: str, qty: int = 1):
        raise NotImplementedError

    def checkout(self, *, idempotency_key: str | None = None):
        """Charge the cart. Must increment self._attempt and apply armed faults."""
        raise NotImplementedError
