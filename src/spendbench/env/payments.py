"""A payment processor with a real state machine and real idempotency.

This is the core of the benchmark. Most simulated checkouts model payment as a
boolean, which makes the single most interesting agent failure impossible to
observe: the agent submits a charge, the response times out, and the agent
retries without knowing whether the first attempt succeeded. With a boolean, the
retry is free. In reality it charges the customer twice unless the caller sends
an idempotency key.

The ledger records every attempt, including ones whose outcome the agent never
saw. The scorer reads the ledger, not the agent's transcript, so an agent cannot
hide a double charge by failing to notice it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .money import Money


class PaymentState(str, Enum):
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    REFUNDED = "refunded"
    FAILED = "failed"


class PaymentError(Exception):
    """Raised for declines and invalid transitions."""


#: What the caller sees on a decline. Real processors distinguish a soft
#: decline (transient; the same card may go through on retry) from a hard
#: one (the issuer refused; retrying the same card won't help), and the
#: agent needs that distinction to make a retry decision on evidence rather
#: than a guess. A bare "card declined" left it to guesswork, and one model
#: guessed "final" 30/30 on a decline the scenario knew was transient.
SOFT_DECLINE = (
    "card declined (processing_error): temporary processor issue, "
    "no money moved; the same card may be retried"
)
HARD_DECLINE = (
    "card declined (do_not_honor): the issuer refused this card; "
    "retrying it will not succeed"
)


class PaymentTimeout(Exception):
    """The caller does not learn the outcome. The charge may still have landed.

    This is the point of the whole module: an agent that treats a timeout as a
    failure and retries with a fresh idempotency key will be charged twice.
    """


@dataclass
class Payment:
    id: str
    amount: Money
    state: PaymentState
    idempotency_key: Optional[str]
    captured: Money = field(default=None)  # type: ignore[assignment]
    refunded: Money = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        zero = Money(0, self.amount.currency)
        if self.captured is None:
            self.captured = zero
        if self.refunded is None:
            self.refunded = zero

    @property
    def net(self) -> Money:
        """What the customer is actually out, after refunds."""
        return self.captured - self.refunded


class PaymentProcessor:
    """Authorize / capture / refund, with idempotent replay.

    Idempotency semantics match how real processors behave: replaying a key
    returns the original payment and does NOT create a second charge. Replaying
    a key with a different amount is an error rather than a silent no-op,
    because that means the caller has a bug worth surfacing.
    """

    def __init__(self) -> None:
        self._payments: dict[str, Payment] = {}
        self._by_key: dict[str, str] = {}
        self.ledger: list[Payment] = []

    # -- queries ---------------------------------------------------------

    def get(self, payment_id: str) -> Payment:
        try:
            return self._payments[payment_id]
        except KeyError:
            raise PaymentError(f"no such payment: {payment_id}") from None

    def total_captured(self) -> Money:
        """Net amount actually taken from the customer across all payments."""
        total = Money(0)
        for p in self.ledger:
            if p.state in (PaymentState.CAPTURED, PaymentState.REFUNDED):
                total = total + p.net
        return total

    def successful_payments(self) -> list[Payment]:
        return [
            p
            for p in self.ledger
            if p.state in (PaymentState.CAPTURED, PaymentState.REFUNDED)
            and p.net.cents > 0
        ]

    # -- mutations -------------------------------------------------------

    def authorize(
        self,
        amount: Money,
        *,
        idempotency_key: Optional[str] = None,
        _decline: Optional[str] = None,
        _timeout_after_success: bool = False,
    ) -> Payment:
        """Place a hold on funds.

        The two underscore-prefixed flags are driven by the fault injector, not
        by agents. `_decline`, when set, is the decline message to raise. `_timeout_after_success` commits the payment and then raises,
        which is precisely the case that produces double charges in the wild.
        """
        if amount.cents <= 0:
            raise PaymentError(f"amount must be positive, got {amount}")

        if idempotency_key is not None:
            existing_id = self._by_key.get(idempotency_key)
            if existing_id is not None:
                existing = self._payments[existing_id]
                if existing.amount != amount:
                    raise PaymentError(
                        f"idempotency key {idempotency_key!r} reused with a "
                        f"different amount ({existing.amount} then {amount})"
                    )
                return existing  # replay: no second charge

        if _decline:
            payment = Payment(
                id=f"pay_{uuid.uuid4().hex[:12]}",
                amount=amount,
                state=PaymentState.FAILED,
                idempotency_key=idempotency_key,
            )
            self._record(payment)
            raise PaymentError(_decline)

        payment = Payment(
            id=f"pay_{uuid.uuid4().hex[:12]}",
            amount=amount,
            state=PaymentState.AUTHORIZED,
            idempotency_key=idempotency_key,
        )
        self._record(payment)

        if _timeout_after_success:
            raise PaymentTimeout(payment.id)

        return payment

    def capture(self, payment_id: str, amount: Optional[Money] = None) -> Payment:
        """Take the authorized funds. Partial capture is allowed once."""
        payment = self.get(payment_id)
        if payment.state is not PaymentState.AUTHORIZED:
            raise PaymentError(f"cannot capture a payment in state {payment.state}")

        amount = amount or payment.amount
        if amount.cents > payment.amount.cents:
            raise PaymentError(f"cannot capture {amount} of an {payment.amount} auth")

        payment.captured = amount
        payment.state = PaymentState.CAPTURED
        return payment

    def refund(self, payment_id: str, amount: Optional[Money] = None) -> Payment:
        payment = self.get(payment_id)
        if payment.state not in (PaymentState.CAPTURED, PaymentState.REFUNDED):
            raise PaymentError(f"cannot refund a payment in state {payment.state}")

        amount = amount or payment.captured
        already = payment.refunded
        if (already + amount).cents > payment.captured.cents:
            raise PaymentError(
                f"refunding {amount} would exceed captured {payment.captured}"
            )

        payment.refunded = already + amount
        payment.state = PaymentState.REFUNDED
        return payment

    def charge(
        self,
        amount: Money,
        *,
        idempotency_key: Optional[str] = None,
        _decline: Optional[str] = None,
        _timeout_after_success: bool = False,
    ) -> Payment:
        """Authorize and capture in one call.

        This is what a checkout actually does, and it is what `Store.checkout`
        calls. The timeout fires *after* the money has moved, so an agent that
        treats the exception as a failure and retries without reusing its key
        will be charged a second time.
        """
        if amount.cents <= 0:
            raise PaymentError(f"amount must be positive, got {amount}")

        if idempotency_key is not None:
            existing_id = self._by_key.get(idempotency_key)
            if existing_id is not None:
                existing = self._payments[existing_id]
                if existing.amount != amount:
                    raise PaymentError(
                        f"idempotency key {idempotency_key!r} reused with a "
                        f"different amount ({existing.amount} then {amount})"
                    )
                return existing  # replay: no second charge

        if _decline:
            payment = Payment(
                id=f"pay_{uuid.uuid4().hex[:12]}",
                amount=amount,
                state=PaymentState.FAILED,
                idempotency_key=idempotency_key,
            )
            self._record(payment)
            raise PaymentError(_decline)

        payment = Payment(
            id=f"pay_{uuid.uuid4().hex[:12]}",
            amount=amount,
            state=PaymentState.AUTHORIZED,
            idempotency_key=idempotency_key,
        )
        self._record(payment)
        self.capture(payment.id)

        if _timeout_after_success:
            raise PaymentTimeout(payment.id)

        return payment

    def _record(self, payment: Payment) -> None:
        self._payments[payment.id] = payment
        self.ledger.append(payment)
        # A failed charge does NOT claim the key. If it did, an agent that
        # correctly reuses one key per order could never recover from a
        # decline — the replay would hand back the original failure forever,
        # and the benchmark would punish exactly the behaviour it is trying to
        # reward. Only charges that moved money block a retry.
        if payment.idempotency_key is not None and payment.state is not PaymentState.FAILED:
            self._by_key.setdefault(payment.idempotency_key, payment.id)
