#!/usr/bin/env python3
"""Tests for the circuit breaker (run-level dispatch bound)."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from circuit_breaker import (  # noqa: E402
    CircuitBreaker, CircuitOpenError, BreakerConfig,
    CLOSED, OPEN, HALF_OPEN,
    TRIP_CONSECUTIVE, TRIP_RATE, TRIP_COST, TRIP_MANUAL,
    estimate_cost,
)

PASS = 0
FAIL = 0


def test(fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  [PASS] {fn.__name__}")
    except Exception as e:
        FAIL += 1
        print(f"  [FAIL] {fn.__name__}: {e}")


def _cb(**cfg):
    tmp = Path(tempfile.mkdtemp()) / "cb.json"
    base = dict(consecutive_failure_threshold=3, failure_rate_threshold=0.5,
                failure_rate_min_samples=4, cost_budget_usd=10.0,
                cooldown_seconds=300, cooldown_max_seconds=3600)
    base.update(cfg)
    return CircuitBreaker(tmp, config=BreakerConfig(**base), run_id="RUN-T")


# ── Basic state ─────────────────────────────────────────────────────────────

def test_starts_closed():
    cb = _cb()
    assert cb.state.state == CLOSED
    allowed, _ = cb.allow("glm-5-2")
    assert allowed


def test_success_keeps_closed():
    cb = _cb()
    for _ in range(10):
        cb.record_success("local-llm")
    assert cb.state.state == CLOSED
    assert cb.state.consecutive_failures == 0


# ── Trip 1: consecutive failures ────────────────────────────────────────────

def test_consecutive_trip():
    cb = _cb(consecutive_failure_threshold=3)
    cb.record_failure("local-llm", "boom")
    cb.record_failure("local-llm", "boom")
    assert cb.state.state == CLOSED, "must not trip before threshold"
    cb.record_failure("local-llm", "boom")
    assert cb.state.state == OPEN
    assert cb.state.trip_reason == TRIP_CONSECUTIVE


def test_success_resets_consecutive():
    # min_samples raised so the RATE breaker cannot fire here — this test
    # isolates the consecutive counter only.
    cb = _cb(consecutive_failure_threshold=3, failure_rate_min_samples=99)
    cb.record_failure("local-llm", "boom")
    cb.record_failure("local-llm", "boom")
    cb.record_success("local-llm")
    assert cb.state.consecutive_failures == 0
    cb.record_failure("local-llm", "boom")
    assert cb.state.state == CLOSED


def test_rate_breaker_still_fires_after_reset():
    """A success resets the streak but NOT the failure-rate history."""
    cb = _cb(consecutive_failure_threshold=3, failure_rate_threshold=0.5,
             failure_rate_min_samples=4)
    cb.record_failure("local-llm", "boom")
    cb.record_failure("local-llm", "boom")
    cb.record_success("local-llm")
    assert cb.state.consecutive_failures == 0
    cb.record_failure("local-llm", "boom")   # 3/4 = 0.75 >= 0.5
    assert cb.state.state == OPEN
    assert cb.state.trip_reason == TRIP_RATE


def test_open_refuses_dispatch():
    cb = _cb(consecutive_failure_threshold=1)
    cb.record_failure("local-llm", "boom")
    assert cb.state.state == OPEN
    allowed, reason = cb.allow("local-llm")
    assert not allowed
    assert "OPEN" in reason
    try:
        cb.before_dispatch("local-llm")
        raise AssertionError("before_dispatch should raise when OPEN")
    except CircuitOpenError:
        pass


# ── Trip 2: failure rate ────────────────────────────────────────────────────

def test_rate_trip():
    cb = _cb(consecutive_failure_threshold=99, failure_rate_threshold=0.5,
             failure_rate_min_samples=4)
    cb.record_failure("local-llm", "x")
    cb.record_success("local-llm")
    cb.record_failure("local-llm", "x")
    assert cb.state.state == CLOSED, "below min samples"
    cb.record_success("local-llm")
    cb.record_failure("local-llm", "x")   # 3/5 = 0.6 >= 0.5
    assert cb.state.state == OPEN
    assert cb.state.trip_reason == TRIP_RATE


def test_rate_needs_min_samples():
    cb = _cb(consecutive_failure_threshold=99, failure_rate_min_samples=10)
    for _ in range(5):
        cb.record_failure("local-llm", "x")
    assert cb.state.state == CLOSED


# ── Trip 3: cost budget ─────────────────────────────────────────────────────

def test_cost_estimate():
    assert estimate_cost("glm-5-2", 1) == 0.35
    assert estimate_cost("swe-1-7", 2) == 2.40
    assert estimate_cost("local-llm", 5) == 0.0
    assert estimate_cost("unknown-model", 1) == 0.5


def test_cost_refuses_before_spend():
    """The gate must refuse BEFORE launching, not after the money is gone."""
    cb = _cb(cost_budget_usd=1.0)
    cb.record_success("glm-5-2")   # 0.35
    cb.record_success("glm-5-2")   # 0.70
    allowed, reason = cb.allow("swe-1-7")   # 0.70 + 1.20 = 1.90 > 1.0
    assert not allowed
    assert "cost budget" in reason
    assert cb.state.cost_spent_usd == 0.70, "refusal must not charge"


def test_cost_trip_on_failure():
    cb = _cb(consecutive_failure_threshold=99, cost_budget_usd=1.0,
             failure_rate_min_samples=99)
    cb.record_failure("swe-1-7", "x")   # 1.20 >= 1.0
    assert cb.state.state == OPEN
    assert cb.state.trip_reason == TRIP_COST


def test_cost_accumulates_on_both_outcomes():
    cb = _cb(cost_budget_usd=100.0)
    cb.record_success("glm-5-2")
    cb.record_failure("glm-5-2", "x")
    assert abs(cb.state.cost_spent_usd - 0.70) < 1e-6


# ── Half-open probe ─────────────────────────────────────────────────────────

def test_half_open_after_cooldown():
    cb = _cb(consecutive_failure_threshold=1, cooldown_seconds=0)
    cb.record_failure("local-llm", "boom")
    assert cb.state.state == OPEN
    allowed, reason = cb.allow("local-llm")
    assert allowed
    assert cb.state.state == HALF_OPEN


def test_half_open_success_closes():
    cb = _cb(consecutive_failure_threshold=1, cooldown_seconds=0)
    cb.record_failure("local-llm", "boom")
    cb.allow("local-llm")            # → HALF_OPEN
    cb.record_success("local-llm")
    assert cb.state.state == CLOSED
    assert cb.state.trip_reason is None


def test_half_open_failure_doubles_cooldown():
    cb = _cb(consecutive_failure_threshold=1, cooldown_seconds=10,
             cooldown_max_seconds=100)
    cb.record_failure("local-llm", "boom")
    cb.state.opened_at = time.time() - 999      # force cooldown elapsed
    cb.allow("local-llm")                       # → HALF_OPEN
    assert cb.state.state == HALF_OPEN
    cb.record_failure("local-llm", "boom again")
    assert cb.state.state == OPEN
    assert cb.state.cooldown_seconds == 20


def test_cooldown_capped():
    cb = _cb(consecutive_failure_threshold=1, cooldown_seconds=80,
             cooldown_max_seconds=100)
    cb.record_failure("local-llm", "boom")
    cb.state.opened_at = time.time() - 999
    cb.allow("local-llm")
    cb.record_failure("local-llm", "boom")
    assert cb.state.cooldown_seconds == 100, cb.state.cooldown_seconds


def test_retry_after_positive_while_open():
    cb = _cb(consecutive_failure_threshold=1, cooldown_seconds=300)
    cb.record_failure("local-llm", "boom")
    assert cb.retry_after() > 250


# ── Persistence ─────────────────────────────────────────────────────────────

def test_state_survives_restart():
    tmp = Path(tempfile.mkdtemp()) / "cb.json"
    cfg = BreakerConfig(consecutive_failure_threshold=2, cooldown_seconds=300)
    cb1 = CircuitBreaker(tmp, config=cfg, run_id="RUN-P")
    cb1.record_failure("glm-5-2", "one")
    cb1.record_failure("glm-5-2", "two")
    assert cb1.state.state == OPEN

    cb2 = CircuitBreaker(tmp, config=cfg)
    assert cb2.state.state == OPEN, "breaker state must survive a process restart"
    assert cb2.state.consecutive_failures == 2
    assert cb2.state.run_id == "RUN-P"
    allowed, _ = cb2.allow("glm-5-2")
    assert not allowed


def test_state_file_is_valid_json():
    cb = _cb()
    cb.record_failure("glm-5-2", "x")
    data = json.loads(cb.state_path.read_text(encoding="utf-8"))
    assert data["state"] == CLOSED
    assert "config" in data and "failure_rate" in data


def test_corrupt_state_file_recovers():
    tmp = Path(tempfile.mkdtemp()) / "cb.json"
    tmp.write_text("{not json", encoding="utf-8")
    cb = CircuitBreaker(tmp)
    assert cb.state.state == CLOSED


# ── Manual control ──────────────────────────────────────────────────────────

def test_manual_trip_and_reset():
    cb = _cb()
    cb.trip("operator stopped the run")
    assert cb.state.state == OPEN
    assert cb.state.trip_reason == TRIP_MANUAL
    cb.reset()
    assert cb.state.state == CLOSED
    assert cb.state.consecutive_failures == 0
    assert cb.state.cost_spent_usd == 0.0
    assert cb.state.run_id == "RUN-T", "reset must preserve run identity"


def test_trip_history_recorded():
    cb = _cb(consecutive_failure_threshold=1, cooldown_seconds=0)
    cb.record_failure("local-llm", "first")
    cb.allow("local-llm")
    cb.record_failure("local-llm", "second")
    assert len(cb.state.trip_history) == 2
    assert cb.state.trip_history[0]["reason"] == TRIP_CONSECUTIVE


def test_report_shape():
    cb = _cb()
    cb.record_failure("glm-5-2", "x")
    r = cb.report()
    for key in ("state", "consecutive_failures", "failure_rate",
                "cost_spent_usd", "cost_budget_usd", "cost_remaining_usd",
                "retry_after_seconds", "trip_count", "run_id"):
        assert key in r, f"missing {key}"
    assert r["cost_remaining_usd"] == 9.65


def test_audit_failure_never_breaks_control_path():
    class BadDb:
        def record_audit(self, *a, **k):
            raise RuntimeError("db down")
    tmp = Path(tempfile.mkdtemp()) / "cb.json"
    cb = CircuitBreaker(tmp, config=BreakerConfig(consecutive_failure_threshold=1),
                        ops_db=BadDb())
    cb.record_failure("glm-5-2", "x")   # must not raise
    assert cb.state.state == OPEN


if __name__ == "__main__":
    print("Circuit Breaker tests")
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            test(fn)
    print(f"\n{PASS} passed, {FAIL} failed")
    raise SystemExit(1 if FAIL else 0)
