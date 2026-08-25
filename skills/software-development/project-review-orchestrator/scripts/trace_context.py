#!/usr/bin/env python3
"""Trace context helpers for the Hermes project-review-orchestrator.

All scripts in the pipeline should carry a ``trace_id`` so that every
artifact, audit event, and subprocess log can be correlated end-to-end.
The canonical trace ID is the Open Design ``run_id``; scripts read it from
``HERMES_TRACE_ID`` in the environment or from an explicit ``--trace-id`` flag.
"""

from __future__ import annotations

import argparse
import os


ENV_TRACE_ID = "HERMES_TRACE_ID"


def add_trace_argument(parser: argparse.ArgumentParser) -> None:
    """Add a standard ``--trace-id`` argument to a script's ArgumentParser."""
    parser.add_argument(
        "--trace-id",
        default=os.environ.get(ENV_TRACE_ID, ""),
        help="Trace/run ID for end-to-end correlation (default: HERMES_TRACE_ID env var)",
    )


def get_trace_id(args: argparse.Namespace | None = None) -> str:
    """Return the trace id from CLI args or the environment."""
    if args is not None and getattr(args, "trace_id", None):
        return str(args.trace_id)
    return os.environ.get(ENV_TRACE_ID, "")
