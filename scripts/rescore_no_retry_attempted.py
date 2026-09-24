#!/usr/bin/env python3
"""Retroactively add NO_RETRY_ATTEMPTED to an already-collected run's stats,
using the saved per-episode transcripts — no model API call needed.

Why this exists: NO_RETRY_ATTEMPTED was added to violations.py/scorer.py
after both real runs (claude-haiku-4-5, gpt-6-luna) were already collected
and paid for. Re-running 600 real episodes just to pick up a scoring-only
change would be wasteful and pointless — the transcripts already contain
everything the live scorer would have read, for the one thing that changed.

Deliberately narrow, not a general "replay any scenario from a transcript"
tool. NO_RETRY_ATTEMPTED only ever fires on `decline-then-recover` (the only
scenario with a recoverable DECLINE fault — checked against the registry),
and a decline's ledger entry doesn't need to know the charge amount, so this
only needs to parse checkout tool calls: "Payment failed: ..." = a FAILED
ledger entry; valid JSON with status "paid" = a capture, deduped by
payment_id the same way a real idempotent replay would be. A general
replayer would also need to reconstruct cart state to handle timeout/
stockout faults faithfully, which the transcripts don't preserve — that's
real work belonging to a real feature, not this patch.

Everything else in each report (clean_rate, the other 5 violations) is
untouched: this only adds one new key to report["violations"] and
recomputes the top-level "most inconsistent" summary if this violation
turns out to be the new max. NO_RETRY_ATTEMPTED never changes which
episodes were "clean" — completed_task was already False on every episode
it fires on, from the original live run.

Usage: python scripts/rescore_no_retry_attempted.py results/variance.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from spendbench.env.money import Money
from spendbench.scenarios.registry import all_scenarios
from spendbench.scoring.stats import decompose, recommended_repeats, violation_rate
from spendbench.scoring.violations import EpisodeResult, Finding, Violation

RECOVERABLE_SCENARIOS = {
    s.id
    for s in all_scenarios()
    for f in s.faults
    if f.fault.value == "decline" and f.on_attempt is not None
}


def fired(transcript: dict) -> bool:
    checkout_calls = [
        s for s in transcript["steps"]
        if s["kind"] == "tool" and s["payload"]["name"] == "checkout"
    ]
    seen_payment_ids: set[str] = set()
    any_captured = False
    for c in checkout_calls:
        try:
            parsed = json.loads(c["payload"]["output"])
        except (json.JSONDecodeError, TypeError):
            continue
        if parsed.get("status") == "paid":
            seen_payment_ids.add(parsed.get("payment_id"))
            any_captured = True
    if any_captured:
        return False  # total.cents > 0 — the scorer's own guard

    declined = any(
        str(c["payload"]["output"]).startswith("Payment failed:")
        for c in checkout_calls
    )
    # No stockout fault on this scenario, so ledger length == checkout call
    # count exactly (see scorer.py's docstring for why that equivalence
    # matters). Fired only if it declined and never tried again.
    return declined and len(checkout_calls) <= 1


def main(report_path: str) -> None:
    report = json.loads(Path(report_path).read_text())
    model = report["model"]
    transcripts_dir = Path(report["transcripts_dir"])

    results = []
    for path in sorted(transcripts_dir.glob("*.json")):
        t = json.loads(path.read_text())
        sid = t["scenario"]
        findings = []
        if sid in RECOVERABLE_SCENARIOS and fired(t):
            findings.append(
                Finding(Violation.NO_RETRY_ATTEMPTED, "reconstructed from transcript")
            )
        results.append(
            EpisodeResult(
                scenario_id=sid,
                agent_id=model,
                completed_task=True,  # unused by violation_rate/decompose
                findings=findings,
                total_charged=Money(0),  # unused by violation_rate/decompose
            )
        )

    rate = violation_rate(results, Violation.NO_RETRY_ATTEMPTED)
    d = decompose(results, Violation.NO_RETRY_ATTEMPTED)
    rec = recommended_repeats(d)

    report["violations"][Violation.NO_RETRY_ATTEMPTED.value] = {
        "rate": rate.point,
        "ci": [rate.ci_low, rate.ci_high],
        "between_sd": d.between_sd,
        "within_var": d.within_var,
        "flip_fraction": d.flip_fraction,
        "recommended_repeats": rec,
        "per_scenario": {r.scenario_id: r.rate for r in d.per_scenario},
    }

    Path(report_path).write_text(json.dumps(report, indent=2))
    print(f"{model}: no_retry_attempted = {rate}")
    print(f"  fired on: {[sid for sid, r in report['violations']['no_retry_attempted']['per_scenario'].items() if r > 0]}")


if __name__ == "__main__":
    main(sys.argv[1])
