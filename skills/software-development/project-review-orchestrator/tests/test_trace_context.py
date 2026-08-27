#!/usr/bin/env python3
"""Unit tests: trace_context.py — trace id propagation."""
import argparse, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import trace_context as tc

P, F = 0, 0
def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1; print(f"  [PASS] {name}")
    else:
        F += 1; print(f"  [FAIL] {name} | {detail}")

print("=" * 60)
print("  trace_context.py")
print("=" * 60)

# explicit --trace-id wins over env
os.environ[tc.ENV_TRACE_ID] = "from-env"
p = argparse.ArgumentParser(); tc.add_trace_argument(p)
a = p.parse_args(["--trace-id", "explicit-1"])
check("explicit --trace-id wins over env", tc.get_trace_id(a) == "explicit-1", tc.get_trace_id(a))

# env used when flag omitted
a2 = p.parse_args([])
check("env used when flag omitted", tc.get_trace_id(a2) == "from-env", tc.get_trace_id(a2))

# no args object -> env
check("get_trace_id(None) reads env", tc.get_trace_id(None) == "from-env")

# empty env -> empty string, never None
os.environ[tc.ENV_TRACE_ID] = ""
p2 = argparse.ArgumentParser(); tc.add_trace_argument(p2)
a3 = p2.parse_args([])
r = tc.get_trace_id(a3)
check("empty env yields '' not None", r == "" and r is not None, repr(r))

# unset env entirely
del os.environ[tc.ENV_TRACE_ID]
p3 = argparse.ArgumentParser(); tc.add_trace_argument(p3)
a4 = p3.parse_args([])
r4 = tc.get_trace_id(a4)
check("unset env yields ''", r4 == "", repr(r4))

# return type is always str
check("return type is str", isinstance(tc.get_trace_id(a4), str))

print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
