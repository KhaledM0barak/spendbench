"""The structured API an agent talks to.

v1 gives the agent a typed API rather than HTML. Structured is reproducible and
keeps the benchmark measuring decision-making instead of parsing skill. The
tradeoff is that prompt injection has to be subtler to be interesting, so it
rides in the `description` field, which is exactly where a real marketplace
would let a seller put arbitrary text.

The agent can only reach this class. It cannot see the payment processor, the
ledger, or product stock counts. Everything the scorer reads comes from the
ledger, so an agent cannot report its way out of a violation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import Catalog
from .faults import Fault, FaultSpec
from .money import Money
from .payments import PaymentError, PaymentProcessor, PaymentTimeout


class StoreError(Exception):
    """Ordinary shopping failure: empty cart, unknown sku, out of stock."""


@dataclass(frozen=True)
class ProductView:
    """What the agent sees. Note there is no stock count, only a boolean."""

    sku: str
    title: str
    price: Money
    in_stock: bool
    description: str


@dataclass(frozen=True)
class CartLine:
    sku: str
    title: str
    unit_price: Money
    qty: int

    @property
    def subtotal(self) -> Money:
        return Money(self.unit_price.cents * self.qty, self.unit_price.currency)


@dataclass(frozen=True)
class CartView:
    lines: tuple[CartLine, ...]

    @property
    def total(self) -> Money:
        total = Money(0)
        for line in self.lines:
            total = total + line.subtotal
        return total


@dataclass(frozen=True)
class Receipt:
    payment_id: str
    total: Money
    skus: tuple[str, ...]


@dataclass(frozen=True)
class Purchase:
    """A charge that actually moved money, and what it bought.

    Recorded even when the agent never saw the outcome, so the scorer can tell
    which SKUs were paid for after a timeout. `acknowledged` is False when the
    response was lost, which is exactly the state a naive agent misreads as
    failure.
    """

    payment_id: str
    total: Money
    skus: tuple[str, ...]
    acknowledged: bool


class Store:
    def __init__(self, catalog: Catalog, faults: tuple[FaultSpec, ...] = ()) -> None:
        self.catalog = catalog
        self.faults = faults
        self.payments = PaymentProcessor()
        self.receipts: list[Receipt] = []
        self.purchases: list[Purchase] = []
        self._cart: dict[str, int] = {}
        self._attempt = 0

    # -- browsing --------------------------------------------------------

    def _view(self, sku: str) -> ProductView:
        p = self.catalog.get(sku)
        return ProductView(
            sku=p.sku,
            title=p.title,
            price=p.price,
            in_stock=p.in_stock,
            description=p.description,
        )

    def search(self, query: str) -> list[ProductView]:
        return [self._view(p.sku) for p in self.catalog.search(query)]

    def view(self, sku: str) -> ProductView:
        try:
            return self._view(sku)
        except KeyError as exc:
            raise StoreError(str(exc)) from None

    # -- cart ------------------------------------------------------------

    def add_to_cart(self, sku: str, qty: int = 1) -> CartView:
        if qty <= 0:
            raise StoreError(f"qty must be positive, got {qty}")
        try:
            product = self.catalog.get(sku)
        except KeyError as exc:
            raise StoreError(str(exc)) from None
        if not product.in_stock:
            raise StoreError(f"{sku} is out of stock")
        self._cart[sku] = self._cart.get(sku, 0) + qty
        return self.cart()

    def remove_from_cart(self, sku: str) -> CartView:
        self._cart.pop(sku, None)
        return self.cart()

    def clear_cart(self) -> CartView:
        self._cart.clear()
        return self.cart()

    def cart(self) -> CartView:
        lines = []
        for sku, qty in self._cart.items():
            p = self.catalog.get(sku)
            lines.append(CartLine(sku=sku, title=p.title, unit_price=p.price, qty=qty))
        return CartView(tuple(lines))

    # -- checkout --------------------------------------------------------

    def _armed(self, fault: Fault, attempt: int) -> FaultSpec | None:
        for spec in self.faults:
            if spec.fault is fault and spec.fires_on(attempt):
                return spec
        return None

    def checkout(self, *, idempotency_key: str | None = None) -> Receipt:
        """Pay for the cart.

        Raises `StoreError` on a stockout, `PaymentError` on a decline, and
        `PaymentTimeout` when the charge landed but the response did not. The
        last one is the interesting case: the cart is *not* cleared, because a
        real client would not know whether to clear it.
        """
        cart = self.cart()
        if not cart.lines:
            raise StoreError("cart is empty")

        self._attempt += 1
        attempt = self._attempt

        if self._armed(Fault.STOCKOUT_AT_CHECKOUT, attempt) is not None:
            # Zero out the cheapest line, the one an agent is most likely to want.
            victim = min(cart.lines, key=lambda line: line.unit_price.cents)
            self.catalog.set_stock(victim.sku, 0)
            self._cart.pop(victim.sku, None)
            raise StoreError(f"{victim.sku} went out of stock during checkout")

        decline = self._armed(Fault.DECLINE, attempt) is not None
        timeout = self._armed(Fault.TIMEOUT_AFTER_SUCCESS, attempt) is not None
        skus = tuple(line.sku for line in cart.lines)

        try:
            payment = self.payments.charge(
                cart.total,
                idempotency_key=idempotency_key,
                _decline=decline,
                _timeout_after_success=timeout,
            )
        except PaymentTimeout as exc:
            # The money moved. Record what it bought before the exception
            # escapes, or the scorer cannot attribute the charge. The cart is
            # deliberately left intact: a real client would not know whether
            # to clear it.
            self._record_purchase(str(exc), cart.total, skus, acknowledged=False)
            raise

        self._record_purchase(payment.id, cart.total, skus, acknowledged=True)
        receipt = Receipt(payment_id=payment.id, total=cart.total, skus=skus)
        self.receipts.append(receipt)
        self._cart.clear()
        return receipt

    def _record_purchase(
        self, payment_id: str, total: Money, skus: tuple[str, ...], *, acknowledged: bool
    ) -> None:
        """Log a landed charge once. Idempotent replays must not log twice."""
        if any(p.payment_id == payment_id for p in self.purchases):
            return
        self.purchases.append(
            Purchase(
                payment_id=payment_id,
                total=total,
                skus=skus,
                acknowledged=acknowledged,
            )
        )


__all__ = [
    "Store",
    "StoreError",
    "ProductView",
    "CartView",
    "CartLine",
    "Receipt",
    "Purchase",
    "PaymentError",
    "PaymentTimeout",
]
