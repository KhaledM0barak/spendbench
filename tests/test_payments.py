"""The payment state machine has to be right, or every DOUBLE_CHARGE finding is
worthless. These tests exist mainly to pin the timeout-then-retry behaviour.
"""

from __future__ import annotations

import pytest

from spendbench.env.money import Money
from spendbench.env.payments import (
    HARD_DECLINE,
    PaymentError,
    PaymentProcessor,
    PaymentState,
    PaymentTimeout,
)


# -- money -----------------------------------------------------------------


def test_money_parses_without_float():
    assert Money.from_dollars("12.34") == Money(1234)
    assert Money.from_dollars("0.05") == Money(5)
    assert Money.from_dollars("7") == Money(700)
    assert Money.from_dollars("-1.50") == Money(-150)


def test_money_rejects_sub_cent_precision():
    with pytest.raises(ValueError):
        Money.from_dollars("1.234")


def test_money_rejects_currency_mixing():
    with pytest.raises(ValueError):
        Money(100, "USD") + Money(100, "CAD")


# -- the happy path --------------------------------------------------------


def test_authorize_then_capture():
    p = PaymentProcessor()
    payment = p.authorize(Money(1999))
    assert payment.state is PaymentState.AUTHORIZED
    assert p.total_captured() == Money(0)

    p.capture(payment.id)
    assert payment.state is PaymentState.CAPTURED
    assert p.total_captured() == Money(1999)


def test_partial_capture_charges_only_what_was_captured():
    p = PaymentProcessor()
    payment = p.authorize(Money(5000))
    p.capture(payment.id, Money(3000))
    assert p.total_captured() == Money(3000)


def test_refund_reduces_net():
    p = PaymentProcessor()
    payment = p.authorize(Money(2500))
    p.capture(payment.id)
    p.refund(payment.id, Money(1000))
    assert p.total_captured() == Money(1500)


def test_cannot_refund_more_than_captured():
    p = PaymentProcessor()
    payment = p.authorize(Money(1000))
    p.capture(payment.id)
    with pytest.raises(PaymentError):
        p.refund(payment.id, Money(1500))


def test_cannot_capture_twice():
    p = PaymentProcessor()
    payment = p.authorize(Money(1000))
    p.capture(payment.id)
    with pytest.raises(PaymentError):
        p.capture(payment.id)


# -- idempotency: the point of the module ----------------------------------


def test_replaying_an_idempotency_key_does_not_charge_twice():
    p = PaymentProcessor()
    first = p.authorize(Money(4200), idempotency_key="order-1")
    second = p.authorize(Money(4200), idempotency_key="order-1")

    assert first.id == second.id
    assert len(p.ledger) == 1

    p.capture(first.id)
    assert p.total_captured() == Money(4200)


def test_reusing_a_key_with_a_different_amount_is_an_error():
    p = PaymentProcessor()
    p.authorize(Money(1000), idempotency_key="order-1")
    with pytest.raises(PaymentError, match="different amount"):
        p.authorize(Money(2000), idempotency_key="order-1")


# -- the failure this benchmark exists to catch ----------------------------


def test_timeout_after_success_leaves_a_real_charge_behind():
    """The agent sees an exception. The money moved anyway."""
    p = PaymentProcessor()
    with pytest.raises(PaymentTimeout):
        p.authorize(Money(3000), _timeout_after_success=True)

    assert len(p.ledger) == 1
    assert p.ledger[0].state is PaymentState.AUTHORIZED


def test_naive_retry_after_timeout_double_charges():
    """No idempotency key: the agent pays twice and cannot see that it did."""
    p = PaymentProcessor()

    with pytest.raises(PaymentTimeout):
        p.authorize(Money(3000), _timeout_after_success=True)
    retry = p.authorize(Money(3000))  # agent assumes the first one failed

    for payment in p.ledger:
        p.capture(payment.id)

    assert len(p.successful_payments()) == 2
    assert p.total_captured() == Money(6000)  # customer is out double
    assert retry.id != p.ledger[0].id


def test_careful_retry_after_timeout_does_not_double_charge():
    """Same timeout, same retry, one idempotency key. This is the correct agent."""
    p = PaymentProcessor()
    key = "order-42"

    with pytest.raises(PaymentTimeout):
        p.authorize(Money(3000), idempotency_key=key, _timeout_after_success=True)
    recovered = p.authorize(Money(3000), idempotency_key=key)

    p.capture(recovered.id)

    assert len(p.successful_payments()) == 1
    assert p.total_captured() == Money(3000)


# -- declines --------------------------------------------------------------


def test_decline_records_a_failed_attempt_but_takes_no_money():
    p = PaymentProcessor()
    with pytest.raises(PaymentError, match="declined"):
        p.authorize(Money(1000), _decline=HARD_DECLINE)

    assert len(p.ledger) == 1
    assert p.ledger[0].state is PaymentState.FAILED
    assert p.total_captured() == Money(0)
    assert p.successful_payments() == []


def test_zero_and_negative_amounts_rejected():
    p = PaymentProcessor()
    for bad in (Money(0), Money(-100)):
        with pytest.raises(PaymentError):
            p.authorize(bad)
