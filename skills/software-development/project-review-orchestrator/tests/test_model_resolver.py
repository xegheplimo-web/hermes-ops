#!/usr/bin/env python3
"""Unit tests: model_resolver.py — never returns a model the provider can't run."""
import json, sys, tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from model_resolver import ModelResolver

P, F = 0, 0
def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1; print(f"  [PASS] {name}")
    else:
        F += 1; print(f"  [FAIL] {name} | {detail}")

print("=" * 60)
print("  model_resolver.py")
print("=" * 60)

r = ModelResolver()
cfg = json.loads((SCRIPTS / "model-roles.json").read_text(encoding="utf-8"))

# every configured stage resolves
stages = list(cfg.get("stages", {}).keys())
check("model-roles.json has stages", len(stages) > 0, f"n={len(stages)}")
bad = []
for s in stages:
    try:
        r.resolve(s)
    except Exception as e:
        bad.append((s, str(e)))
check("every stage resolves", not bad, str(bad))

# CRITICAL invariant: primary is always in provider_valid_models when the set exists
pvm = cfg.get("provider_valid_models", {})
viol = []
for s in stages:
    a = r.resolve(s)
    valid = set(pvm.get(a.provider, []))
    if valid and a.primary not in valid:
        viol.append((s, a.provider, a.primary))
check("primary always provider-valid", not viol, str(viol))

# fallbacks must also be provider-valid
fviol = []
for s in stages:
    a = r.resolve(s)
    valid = set(pvm.get(a.provider, []))
    if valid:
        fviol += [(s, m) for m in a.fallbacks if m not in valid]
check("all fallbacks provider-valid", not fviol, str(fviol))

# every risk level maps to a stage
risks = list(cfg.get("risk_to_stage", {}).keys())
rbad = []
for risk in risks:
    try:
        r.resolve(risk)
    except Exception as e:
        rbad.append((risk, str(e)))
check("every risk_to_stage key resolves", not rbad, str(rbad))

# resolve_for_task works for the canonical risk ladder
tbad = []
for risk in ["TRIVIAL", "LOW", "MEDIUM", "HIGH", "CRITICAL"]:
    try:
        a = r.resolve_for_task(risk)
        if not a.primary:
            tbad.append((risk, "empty primary"))
    except Exception as e:
        tbad.append((risk, str(e)))
check("resolve_for_task covers full risk ladder", not tbad, str(tbad))

# unknown risk must ESCALATE (not crash, not silently cheapen)
a_unknown = r.resolve_for_task("WEIRD-RISK-NAME")
a_crit = r.resolve_for_task("CRITICAL")
check("unknown risk escalates instead of raising",
      a_unknown.stage == a_crit.stage, f"{a_unknown.stage} vs {a_crit.stage}")

# TRIVIAL must not be more expensive than LOW
check("TRIVIAL maps to the trivial stage",
      r.resolve_for_task("TRIVIAL").stage == r.resolve_for_task("LOW").stage,
      r.resolve_for_task("TRIVIAL").stage)

# unknown stage must raise, not silently default
try:
    r.resolve("no-such-stage-xyz")
    check("unknown stage raises", False, "returned silently")
except ValueError:
    check("unknown stage raises ValueError", True)
except Exception as e:
    check("unknown stage raises ValueError", False, type(e).__name__)

# all_models = primary + fallbacks, primary first, no dupes
a = r.resolve(stages[0])
am = a.all_models
check("all_models starts with primary", am and am[0] == a.primary, str(am))
check("all_models has no duplicates", len(am) == len(set(am)), str(am))

# missing config file must raise FileNotFoundError, not return junk
try:
    ModelResolver(str(Path(tempfile.gettempdir()) / "definitely-missing-xyz.json"))
    check("missing config raises", False, "returned silently")
except FileNotFoundError:
    check("missing config raises FileNotFoundError", True)
except Exception as e:
    check("missing config raises FileNotFoundError", False, type(e).__name__)

# a provider whose valid set excludes everything still yields a primary (failsafe)
tmp = Path(tempfile.mkdtemp())
fake = tmp / "m.json"
fake.write_text(json.dumps({
    "stages": {"s1": {"provider": "devin", "primary_model": "ghost-1",
                      "fallback_chain": ["ghost-2"], "executor": "devin"}},
    "provider_valid_models": {"devin": ["real-1"]},
    "risk_to_stage": {"LOW": "s1"},
}), encoding="utf-8")
a2 = ModelResolver(str(fake)).resolve("s1")
check("failsafe keeps a primary when nothing is valid", a2.primary == "ghost-1", a2.primary)
check("failsafe records preferred separately", a2.preferred == "ghost-1", a2.preferred)

print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
