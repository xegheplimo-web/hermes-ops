#!/usr/bin/env python3
"""
State machine manager for project-review-orchestrator review runs.

Validates state transitions, creates new state files on first use, and
prints machine-readable JSON status after each transition.

States (in order):
  CREATED → PREFLIGHT → EVIDENCE_COLLECTED → HERMES_ANALYSIS_DONE →
  PACKET_BUILT → EXTERNAL_REVIEW_REQUESTED → EXTERNAL_REVIEW_RECEIVED →
  RECONCILED → CODEMAP_BUILT → TASKS_DECOMPOSED →
  PLAN_READY_NOT_DISPATCHED → DISPATCHED → IN_PROGRESS → COMPLETED

FAILED can transition from any non-terminal state.
AWAITING_HUMAN_EXTERNAL_REVIEW is a pause state that branches off
  EXTERNAL_REVIEW_REQUESTED and returns to EXTERNAL_REVIEW_RECEIVED.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import secrets
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# State machine definition
# ---------------------------------------------------------------------------

ORDERED_STATES = [
    "CREATED",
    "PREFLIGHT",
    "EVIDENCE_COLLECTED",
    "HERMES_ANALYSIS_DONE",
    "PACKET_BUILT",
    "EXTERNAL_REVIEW_REQUESTED",
    "EXTERNAL_REVIEW_RECEIVED",
    "RECONCILED",
    "CODEMAP_BUILT",
    "TASKS_DECOMPOSED",
    "PLAN_READY_NOT_DISPATCHED",
    "DISPATCHED",
    "IN_PROGRESS",
    "COMPLETED",
]

STATE_ORDER: dict[str, int] = {s: i for i, s in enumerate(ORDERED_STATES)}

TERMINAL = frozenset({"COMPLETED"})
PAUSE_STATE = "AWAITING_HUMAN_EXTERNAL_REVIEW"
FAILED_STATE = "FAILED"

# Valid transitions beyond the simple forward order.
# key = current state, value = set of allowed next states.
SPECIAL_TRANSITIONS: dict[str, set[str]] = {
    "EXTERNAL_REVIEW_REQUESTED": {PAUSE_STATE},
    PAUSE_STATE: {"EXTERNAL_REVIEW_RECEIVED"},
}


def _initial_state() -> dict[str, Any]:
    """Return the default state for a brand-new review run."""
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    run_id = now.strftime("%Y-%m-%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    return {
        "run_id": run_id,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "status": "CREATED",
        "project": "",
        "branch": "",
        "commit_sha": "",
        "dirty": False,
        "review_mode": "openai-api",
        "error": None,
        "progress_pct": 0,
        "artifacts": {},
    }


def _ensure_state(state_path: Path) -> dict[str, Any]:
    """Read existing state or create a fresh one."""
    if state_path.is_file():
        return json.loads(state_path.read_text(encoding="utf-8"))
    state = _initial_state()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return state


def _validate_transition(
    current: str, desired: str, *, has_error: bool
) -> tuple[bool, str]:
    """
    Validate whether a transition from *current* to *desired* is legal.

    Returns (valid, reason).  If valid is True the reason is empty.
    """
    if current == desired:
        return True, ""

    # FAILED is reachable from any non-terminal state
    if desired == FAILED_STATE:
        if current in TERMINAL:
            return False, f"Cannot transition from terminal state '{current}' to FAILED"
        return True, ""

    # Cannot leave a terminal state
    if current in TERMINAL:
        return False, f"State '{current}' is terminal — no further transitions allowed"

    # Check special transitions (pause state branching)
    if current in SPECIAL_TRANSITIONS and desired in SPECIAL_TRANSITIONS[current]:
        return True, ""

    # If we're in the pause state, the only exit is the special one already checked
    if current == PAUSE_STATE:
        return (
            False,
            f"From '{PAUSE_STATE}' the only valid transition is "
            f"to 'EXTERNAL_REVIEW_RECEIVED'",
        )

    # Standard forward progression
    cur_idx = STATE_ORDER.get(current)
    des_idx = STATE_ORDER.get(desired)

    if cur_idx is None:
        return False, f"Unknown current state '{current}'"
    if des_idx is None:
        return False, f"Unknown desired state '{desired}'"

    # If we're past the pause-state branch, handle the gap:
    # EXTERNAL_REVIEW_RECEIVED can be reached from RECONCILED or AWAITING...
    # Actually RECONCILED is after EXTERNAL_REVIEW_RECEIVED, so no.
    # From PAUSE_STATE → EXTERNAL_REVIEW_RECEIVED is the only way forward.
    # All other forward moves: des_idx must be > cur_idx

    if des_idx < cur_idx:
        return False, (
            f"Cannot transition backward from '{current}' "
            f"(index {cur_idx}) to '{desired}' (index {des_idx})"
        )

    # Jumping forward is allowed (skip intermediate states)
    return True, ""


def _apply_transition(
    state: dict[str, Any],
    new_status: str,
    error: str | None,
    progress: int | None,
) -> dict[str, Any]:
    """Mutate *state* in place and return it."""
    state["status"] = new_status
    state["updated_at"] = (
        dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    )

    if error is not None:
        state["error"] = error

    if progress is not None:
        state["progress_pct"] = max(0, min(100, progress))

    return state


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage project-review-orchestrator review run state machine."
    )
    parser.add_argument(
        "--state-file",
        required=True,
        help="Path to state.json (created if it doesn't exist)",
    )
    parser.add_argument(
        "--status",
        required=True,
        help="Target state name (e.g. PREFLIGHT, RECONCILED, FAILED)",
    )
    parser.add_argument(
        "--error",
        default=None,
        help="Error message (only meaningful when transitioning to FAILED)",
    )
    parser.add_argument(
        "--progress",
        type=int,
        default=None,
        help="Progress percentage 0-100 (optional, stored with any state)",
    )
    args = parser.parse_args()

    new_status = args.status.upper().strip()

    try:
        state_path = Path(args.state_file).resolve()
        state = _ensure_state(state_path)
        previous_status = state["status"]

        # Validate transition
        valid, reason = _validate_transition(
            previous_status, new_status, has_error=args.error is not None
        )
        if not valid:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"Invalid transition: {reason}",
                        "previous_status": previous_status,
                        "new_status": new_status,
                    },
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 1

        # Prevent --error without FAILED
        if args.error is not None and new_status != FAILED_STATE:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "--error is only valid when transitioning to FAILED",
                        "previous_status": previous_status,
                        "new_status": new_status,
                    },
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 1

        # Apply
        state = _apply_transition(state, new_status, args.error, args.progress)
        state_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        print(
            json.dumps(
                {
                    "ok": True,
                    "run_id": state["run_id"],
                    "previous_status": previous_status,
                    "new_status": new_status,
                },
                indent=2,
            )
        )
        return 0

    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())