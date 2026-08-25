#!/usr/bin/env python3
"""
Circuit breaker for the DevinAdapter dispatch path.

The Ops DB already enforces a PER-TASK attempt bound (`attempts < max_attempts`
inside claim_task). That is necessary but not sufficient: a systemic failure —
Devin binary broken, model unavailable, auth expired, repo unbuildable —
burns every task's attempt budget in sequence and blows the token/cost budget
on identical failures. This module adds the RUN-LEVEL bound.

Three independent trip conditions:

  1. consecutive_failures >= threshold   → fail-fast on a systemic fault
  2. failure_rate >= threshold (after a minimum sample) → degraded provider
  3. cost_spent >= budget                → cost-bounded, not just count-bounded

States: CLOSED (dispatch allowed) → OPEN (dispatch refused) → HALF_OPEN
(one probe allowed after cooldown). A probe success closes the breaker; a
probe failure re-opens it with the cooldown doubled, capped.

State is persisted next to the run artifacts so it survives process restarts,
and every trip/reset is recorded as an Ops DB audit event when available.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

try:
    from trace_context import add_trace_argument, get_trace_id
    _HAS_TRACE = True
except ImportError:
    _HAS_TRACE = False


# ── States ──────────────────────────────────────────────────────────────────

CLOSED = "CLOSED"
OPEN = "OPEN"
HALF_OPEN = "HALF_OPEN"
ALL_STATES = [CLOSED, OPEN, HALF_OPEN]

# Trip reasons
TRIP_CONSECUTIVE = "consecutive_failures"
TRIP_RATE = "failure_rate"
TRIP_COST = "cost_budget_exhausted"
TRIP_MANUAL = "manual_trip"

# Rough per-attempt cost estimate in USD, keyed by Devin model. Deliberately
# coarse: the breaker needs an upper bound, not an invoice.
_MODEL_COST: dict[str, float] = {
    "glm-5-2": 0.35,
    "swe-1-7": 1.20,
    "claude-3-5-sonnet": 0.90,
    "o3": 2.50,
    "gpt-4o": 0.80,
    "gpt-4o-mini": 0.06,
    "claude-haiku": 0.06,
    "local-llm": 0.0,
}
_DEFAULT_COST = 0.50


def estimate_cost(model: str, attempts: int = 1) -> float:
    """Coarse upper-bound cost estimate for N attempts on a model."""
    return round(_MODEL_COST.get(model, _DEFAULT_COST) * max(attempts, 0), 4)


# ── Config ──────────────────────────────────────────────────────────────────


@dataclass
class BreakerConfig:
    """Trip thresholds. Defaults are deliberately conservative."""
    consecutive_failure_threshold: int = 3
    failure_rate_threshold: float = 0.5
    failure_rate_min_samples: int = 4
    cost_budget_usd: float = 10.0
    cooldown_seconds: int = 300
    cooldown_max_seconds: int = 3600

    @classmethod
    def from_env(cls) -> BreakerConfig:
        def _f(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, default))
            except (TypeError, ValueError):
                return default

        return cls(
            consecutive_failure_threshold=int(_f("HERMES_CB_CONSECUTIVE", 3)),
            failure_rate_threshold=_f("HERMES_CB_RATE", 0.5),
            failure_rate_min_samples=int(_f("HERMES_CB_MIN_SAMPLES", 4)),
            cost_budget_usd=_f("HERMES_CB_COST_BUDGET", 10.0),
            cooldown_seconds=int(_f("HERMES_CB_COOLDOWN", 300)),
            cooldown_max_seconds=int(_f("HERMES_CB_COOLDOWN_MAX", 3600)),
        )


@dataclass
class BreakerState:
    state: str = CLOSED
    consecutive_failures: int = 0
    total_attempts: int = 0
    total_failures: int = 0
    cost_spent_usd: float = 0.0
    opened_at: float | None = None
    cooldown_seconds: int = 300
    trip_reason: str | None = None
    trip_history: list[dict] = field(default_factory=list)
    last_error: str | None = None
    run_id: str | None = None
    trace_id: str | None = None

    @property
    def failure_rate(self) -> float:
        if self.total_attempts == 0:
            return 0.0
        return round(self.total_failures / self.total_attempts, 4)


class CircuitOpenError(RuntimeError):
    """Raised when dispatch is attempted while the breaker is OPEN."""

    def __init__(self, state: BreakerState, retry_after: float):
        self.state = state
        self.retry_after = retry_after
        super().__init__(
            f"Circuit breaker OPEN ({state.trip_reason}): "
            f"{state.consecutive_failures} consecutive failures, "
            f"rate={state.failure_rate}, spent=${state.cost_spent_usd:.2f}. "
            f"Retry after {retry_after:.0f}s."
        )


# ── Breaker ─────────────────────────────────────────────────────────────────


class CircuitBreaker:
    """Run-level circuit breaker with persisted state.

    Usage:
        cb = CircuitBreaker(state_path, run_id="RUN-1")
        cb.before_dispatch(model="glm-5-2")   # raises CircuitOpenError if OPEN
        ...launch Devin...
        cb.record_success(model="glm-5-2") | cb.record_failure(model=..., error=...)
    """

    def __init__(
        self,
        state_path: str | Path | None = None,
        config: BreakerConfig | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        ops_db: Any = None,
    ):
        self.config = config or BreakerConfig.from_env()
        self.state_path = Path(state_path) if state_path else None
        self.ops_db = ops_db
        self.state = self._load()
        if run_id:
            self.state.run_id = run_id
        if trace_id:
            self.state.trace_id = trace_id
        if not self.state.cooldown_seconds:
            self.state.cooldown_seconds = self.config.cooldown_seconds

    # ── Persistence ─────────────────────────────────────────────────────

    def _load(self) -> BreakerState:
        if self.state_path and self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                known = {f for f in BreakerState.__dataclass_fields__}
                return BreakerState(**{k: v for k, v in raw.items() if k in known})
            except (json.JSONDecodeError, OSError, TypeError):
                pass
        return BreakerState(cooldown_seconds=self.config.cooldown_seconds)

    def save(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self.state)
        payload["failure_rate"] = self.state.failure_rate
        payload["config"] = asdict(self.config)
        self.state_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ── Audit ───────────────────────────────────────────────────────────

    def _audit(self, action: str, detail: dict) -> None:
        if not self.ops_db:
            return
        try:
            from ops_adapter import AuditEvent
            detail = dict(detail)
            detail.setdefault("trace_id", self.state.trace_id or "")
            detail.setdefault("run_id", self.state.run_id or "")
            self.ops_db.record_audit(AuditEvent(
                actor="hermes.circuit_breaker", action=action, detail=detail,
            ))
        except Exception:
            # Audit must never break the control path.
            pass

    # ── Gate ────────────────────────────────────────────────────────────

    def retry_after(self) -> float:
        """Seconds remaining on the cooldown; 0 when the probe is due."""
        if self.state.state != OPEN or self.state.opened_at is None:
            return 0.0
        elapsed = time.time() - self.state.opened_at
        return max(0.0, self.state.cooldown_seconds - elapsed)

    def allow(self, model: str = "", attempts: int = 1) -> tuple[bool, str]:
        """Non-raising gate check. Returns (allowed, reason)."""
        # Cost is checked ahead of the launch so an over-budget attempt
        # is refused BEFORE tokens are spent, not after.
        projected = self.state.cost_spent_usd + estimate_cost(model, attempts)
        if projected > self.config.cost_budget_usd:
            return False, (
                f"cost budget would be exceeded: ${projected:.2f} > "
                f"${self.config.cost_budget_usd:.2f}"
            )

        if self.state.state == CLOSED:
            return True, "closed"

        if self.state.state == HALF_OPEN:
            return True, "half-open probe"

        # OPEN
        remaining = self.retry_after()
        if remaining <= 0:
            self.state.state = HALF_OPEN
            self.save()
            self._audit("breaker_half_open", {
                "cooldown_seconds": self.state.cooldown_seconds,
                "trip_reason": self.state.trip_reason,
            })
            return True, "cooldown elapsed → half-open probe"
        return False, f"OPEN ({self.state.trip_reason}), retry after {remaining:.0f}s"

    def before_dispatch(self, model: str = "", attempts: int = 1) -> None:
        """Raise CircuitOpenError when dispatch must not proceed."""
        allowed, reason = self.allow(model, attempts)
        if not allowed:
            self.state.trip_reason = self.state.trip_reason or TRIP_COST
            if self.state.state != OPEN:
                self._trip(TRIP_COST, reason)
            raise CircuitOpenError(self.state, self.retry_after())

    # ── Outcome recording ───────────────────────────────────────────────

    def record_success(self, model: str = "", attempts: int = 1) -> BreakerState:
        self.state.total_attempts += 1
        self.state.consecutive_failures = 0
        self.state.cost_spent_usd = round(
            self.state.cost_spent_usd + estimate_cost(model, attempts), 4
        )
        if self.state.state == HALF_OPEN:
            # Probe succeeded: close and reset the cooldown.
            self.state.state = CLOSED
            self.state.opened_at = None
            self.state.trip_reason = None
            self.state.cooldown_seconds = self.config.cooldown_seconds
            self._audit("breaker_closed", {"reason": "half-open probe succeeded"})
        self.save()
        return self.state

    def record_failure(
        self, model: str = "", error: str = "", attempts: int = 1,
    ) -> BreakerState:
        self.state.total_attempts += 1
        self.state.total_failures += 1
        self.state.consecutive_failures += 1
        self.state.last_error = (error or "")[:500]
        self.state.cost_spent_usd = round(
            self.state.cost_spent_usd + estimate_cost(model, attempts), 4
        )

        was_half_open = self.state.state == HALF_OPEN

        if was_half_open:
            # Probe failed: re-open with doubled cooldown (capped).
            self.state.cooldown_seconds = min(
                self.state.cooldown_seconds * 2, self.config.cooldown_max_seconds
            )
            self._trip(self.state.trip_reason or TRIP_CONSECUTIVE,
                       "half-open probe failed")
        elif self.state.consecutive_failures >= self.config.consecutive_failure_threshold:
            self._trip(TRIP_CONSECUTIVE, (
                f"{self.state.consecutive_failures} consecutive failures "
                f">= {self.config.consecutive_failure_threshold}"
            ))
        elif (
            self.state.total_attempts >= self.config.failure_rate_min_samples
            and self.state.failure_rate >= self.config.failure_rate_threshold
        ):
            self._trip(TRIP_RATE, (
                f"failure rate {self.state.failure_rate} >= "
                f"{self.config.failure_rate_threshold} over "
                f"{self.state.total_attempts} attempts"
            ))
        elif self.state.cost_spent_usd >= self.config.cost_budget_usd:
            self._trip(TRIP_COST, (
                f"spent ${self.state.cost_spent_usd:.2f} >= budget "
                f"${self.config.cost_budget_usd:.2f}"
            ))

        self.save()
        return self.state

    def _trip(self, reason: str, detail: str) -> None:
        self.state.state = OPEN
        self.state.opened_at = time.time()
        self.state.trip_reason = reason
        record = {
            "at": self.state.opened_at,
            "reason": reason,
            "detail": detail,
            "consecutive_failures": self.state.consecutive_failures,
            "failure_rate": self.state.failure_rate,
            "cost_spent_usd": self.state.cost_spent_usd,
            "cooldown_seconds": self.state.cooldown_seconds,
        }
        self.state.trip_history.append(record)
        self._audit("breaker_tripped", record)

    # ── Manual control ──────────────────────────────────────────────────

    def trip(self, detail: str = "manual") -> BreakerState:
        self._trip(TRIP_MANUAL, detail)
        self.save()
        return self.state

    def reset(self) -> BreakerState:
        prior = self.state.state
        self.state = BreakerState(
            cooldown_seconds=self.config.cooldown_seconds,
            run_id=self.state.run_id,
            trace_id=self.state.trace_id,
        )
        self.save()
        self._audit("breaker_reset", {"prior_state": prior})
        return self.state

    # ── Reporting ───────────────────────────────────────────────────────

    def report(self) -> dict:
        return {
            "state": self.state.state,
            "consecutive_failures": self.state.consecutive_failures,
            "total_attempts": self.state.total_attempts,
            "total_failures": self.state.total_failures,
            "failure_rate": self.state.failure_rate,
            "cost_spent_usd": self.state.cost_spent_usd,
            "cost_budget_usd": self.config.cost_budget_usd,
            "cost_remaining_usd": round(
                max(0.0, self.config.cost_budget_usd - self.state.cost_spent_usd), 4
            ),
            "trip_reason": self.state.trip_reason,
            "retry_after_seconds": round(self.retry_after(), 1),
            "trip_count": len(self.state.trip_history),
            "last_error": self.state.last_error,
            "run_id": self.state.run_id,
            "trace_id": self.state.trace_id,
        }


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect/control the dispatch circuit breaker.")
    parser.add_argument("--state-file", required=True, help="Path to circuit-breaker.json")
    parser.add_argument("--action", default="status",
                        choices=["status", "check", "reset", "trip",
                                 "record-success", "record-failure"])
    parser.add_argument("--model", default="", help="Model for cost accounting")
    parser.add_argument("--error", default="", help="Error text for record-failure")
    parser.add_argument("--run-id", help="Review run ID")
    if _HAS_TRACE:
        add_trace_argument(parser)
    args = parser.parse_args()
    trace_id = get_trace_id(args) if _HAS_TRACE else os.environ.get("HERMES_TRACE_ID", "")

    cb = CircuitBreaker(args.state_file, run_id=args.run_id, trace_id=trace_id)

    if args.action == "status":
        print(json.dumps({"ok": True, **cb.report()}, indent=2))
        return 0
    if args.action == "check":
        allowed, reason = cb.allow(args.model)
        print(json.dumps({"ok": True, "allowed": allowed, "reason": reason, **cb.report()}, indent=2))
        return 0 if allowed else 4
    if args.action == "reset":
        cb.reset()
        print(json.dumps({"ok": True, "action": "reset", **cb.report()}, indent=2))
        return 0
    if args.action == "trip":
        cb.trip("manual CLI trip")
        print(json.dumps({"ok": True, "action": "trip", **cb.report()}, indent=2))
        return 0
    if args.action == "record-success":
        cb.record_success(args.model)
        print(json.dumps({"ok": True, **cb.report()}, indent=2))
        return 0
    if args.action == "record-failure":
        cb.record_failure(args.model, args.error)
        print(json.dumps({"ok": True, **cb.report()}, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
