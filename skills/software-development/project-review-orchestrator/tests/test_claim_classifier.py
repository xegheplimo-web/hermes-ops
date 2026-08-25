#!/usr/bin/env python3
"""Tests for the FACT / INFERENCE / UNKNOWN claim classifier."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from claim_classifier import (  # noqa: E402
    FACT, INFERENCE, UNKNOWN, ALL_LABELS,
    classify_claim, classify_document, to_investigation_findings,
    extract_evidence_refs, is_trivial, _load_known_paths,
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


def label(claim, known=None):
    return classify_claim(claim, known)["label"]


# ── FACT ────────────────────────────────────────────────────────────────────

def test_fact_from_file_path():
    assert label("The dispatcher lives in dispatch_to_devin.py and claims tasks") == FACT


def test_fact_from_evidence_marker():
    assert label("Auth is enforced at the adapter boundary [E:ops_adapter.claim_task]") == FACT


def test_fact_from_line_ref():
    assert label("The transition guard rejects invalid states at line 352") == FACT


def test_fact_from_test_ref():
    assert label("Covered by test_consecutive_trip in the breaker suite") == FACT


def test_fact_from_commit_sha():
    assert label("Introduced in commit a1b2c3d4e5f6 of the review pipeline") == FACT


# ── INFERENCE ───────────────────────────────────────────────────────────────

def test_inference_from_hedge():
    assert label("The queue likely leaks leases when a worker dies mid-flight") == INFERENCE


def test_inference_explicit_label():
    assert label("INFERENCE: the retry path shares the same lock as the claim path") == INFERENCE


def test_inference_unverified_label():
    assert label("UNVERIFIED: cost accounting matches the provider invoice") == INFERENCE


def test_hedged_claim_with_evidence_is_inference():
    """Evidence proves existence, not interpretation — hedging still downgrades."""
    r = classify_claim("ops_adapter.py appears to serialize every claim")
    assert r["label"] == INFERENCE
    assert r["evidence_refs"], "evidence should still be captured"


def test_fact_label_without_evidence_is_downgraded():
    r = classify_claim("FACT: every dispatch carries a lease")
    assert r["label"] == INFERENCE
    assert any("downgraded" in x for x in r["reasons"])


# ── UNKNOWN ─────────────────────────────────────────────────────────────────

def test_unknown_explicit():
    assert label("UNKNOWN: whether the gateway retries on 502") == UNKNOWN


def test_unknown_from_ignorance_language():
    assert label("It is unclear how the repair loop bounds its token spend") == UNKNOWN


def test_unknown_needs_investigation():
    assert label("The merge path needs investigation before we touch it") == UNKNOWN


def test_unknown_bare_assertion():
    """Assertive, non-trivial, no evidence, no reasoning → UNKNOWN, not FACT."""
    assert label("The whole verification stage is broken in production") == UNKNOWN


def test_unknown_blocks_implementation():
    r = classify_claim("UNKNOWN: whether the policy gate can be bypassed")
    assert r["blocks_implementation"] is True
    assert r["requires_verification"] is True


def test_fact_does_not_block():
    r = classify_claim("The breaker state is persisted in circuit_breaker.py")
    assert r["blocks_implementation"] is False
    assert r["requires_verification"] is False


# ── Strict path resolution ──────────────────────────────────────────────────

def test_unresolvable_path_is_not_evidence():
    """A hallucinated filename must not launder an inference into a fact."""
    known = {"scripts/ops_adapter.py"}
    r = classify_claim("The retry logic is in scripts/imaginary_module.py", known)
    assert r["label"] != FACT, f"got {r['label']}"
    assert r["evidence_refs"] == []


def test_resolvable_path_counts_as_evidence():
    known = {"scripts/ops_adapter.py"}
    r = classify_claim("The claim path is in scripts/ops_adapter.py", known)
    assert r["label"] == FACT
    assert r["evidence_refs"]


def test_path_suffix_match():
    known = {"skills/x/scripts/ops_adapter.py"}
    r = classify_claim("See ops_adapter.py for the lease logic", known)
    assert r["label"] == FACT


def test_extract_refs_dedupes():
    refs = extract_evidence_refs("ops_adapter.py and ops_adapter.py again")
    assert len(refs) == 1


# ── Trivial text ────────────────────────────────────────────────────────────

def test_heading_is_trivial():
    assert is_trivial("## PROJECT SNAPSHOT")


def test_table_row_is_trivial():
    assert is_trivial("| col | col |")


def test_short_text_is_trivial():
    assert is_trivial("ok")


def test_trivial_marked_and_non_blocking():
    r = classify_claim("## SECURITY STATUS")
    assert r["trivial"] is True
    assert r["blocks_implementation"] is False


# ── Document pass ───────────────────────────────────────────────────────────

DOC = """# PROJECT SNAPSHOT

The dispatcher is implemented in dispatch_to_devin.py and claims via Ops DB.

## RISKS

- It is unclear whether the repair loop bounds its cost.
- The queue likely drops leases when a worker crashes.
- Covered by test_state_survives_restart in the breaker suite.

```python
this_is_code = "should be skipped"
```

## UNKNOWN AREAS

UNKNOWN: whether the merge gate exists at all.
"""


def test_document_counts():
    doc = classify_document(DOC)
    assert doc["counts"][FACT] == 2, doc["counts"]
    assert doc["counts"][INFERENCE] == 1, doc["counts"]
    assert doc["counts"][UNKNOWN] == 2, doc["counts"]


def test_document_skips_code_fence():
    doc = classify_document(DOC)
    assert not any("this_is_code" in c["claim"] for c in doc["claims"])


def test_document_tracks_section():
    doc = classify_document(DOC)
    unknown = [c for c in doc["claims"] if c["label"] == UNKNOWN]
    assert any(c["section"] == "UNKNOWN AREAS" for c in unknown), \
        [c["section"] for c in unknown]


def test_document_records_line_numbers():
    doc = classify_document(DOC)
    assert all(isinstance(c["line"], int) and c["line"] > 0 for c in doc["claims"])


def test_grounded_when_facts_dominate():
    good = "\n".join([
        "The adapter is in ops_adapter.py at line 298.",
        "The breaker is in circuit_breaker.py at line 140.",
        "Covered by test_cost_trip_on_failure in the suite.",
        "Routing lives in strategy_router.py at line 12.",
    ])
    doc = classify_document(good)
    assert doc["analysis_grounded"] is True
    assert doc["unknown_ratio"] == 0.0


def test_ungrounded_when_unknowns_dominate():
    bad = "\n".join([
        "The whole thing is broken everywhere in production.",
        "It is unclear how any of this works at all.",
        "Every module fails under load without exception.",
    ])
    doc = classify_document(bad)
    assert doc["analysis_grounded"] is False
    assert doc["unknown_ratio"] > 0.25


def test_no_facts_is_not_grounded():
    doc = classify_document("The system probably works as intended somehow.")
    assert doc["analysis_grounded"] is False


def test_investigation_list_populated():
    doc = classify_document(DOC)
    assert len(doc["investigation_required"]) == doc["counts"][UNKNOWN]
    assert all("line" in i and "claim" in i for i in doc["investigation_required"])


# ── Findings handoff ────────────────────────────────────────────────────────

def test_investigation_findings_shape():
    doc = classify_document(DOC)
    findings = to_investigation_findings(doc)
    assert findings, "UNKNOWN claims must yield findings"
    f = findings[0]
    for key in ("id", "title", "claim", "final_position", "final_severity",
                "evidence_refs", "epistemic_label"):
        assert key in f, f"missing {key}"
    assert f["final_position"] == "UNVERIFIED"
    assert f["epistemic_label"] == UNKNOWN
    assert f["title"].startswith("Investigate:")


def test_facts_never_become_investigations():
    doc = classify_document("The adapter is in ops_adapter.py at line 12.")
    assert to_investigation_findings(doc) == []


# ── Evidence loading ────────────────────────────────────────────────────────

def test_load_known_paths_from_evidence():
    tmp = Path(tempfile.mkdtemp()) / "repo-evidence.json"
    tmp.write_text(json.dumps({
        "files": [{"path": "scripts/ops_adapter.py"}, {"path": "./scripts/a.py"}],
        "nested": {"tree": {"file": "src/main.ts"}},
    }), encoding="utf-8")
    paths = _load_known_paths(tmp)
    assert "scripts/ops_adapter.py" in paths
    assert "scripts/a.py" in paths
    assert "src/main.ts" in paths


def test_load_known_paths_missing_file():
    assert _load_known_paths(Path("does/not/exist.json")) is None


def test_load_known_paths_corrupt():
    tmp = Path(tempfile.mkdtemp()) / "bad.json"
    tmp.write_text("{broken", encoding="utf-8")
    assert _load_known_paths(tmp) is None


# ── Contract ────────────────────────────────────────────────────────────────

def test_every_label_is_valid():
    doc = classify_document(DOC)
    assert all(c["label"] in ALL_LABELS for c in doc["claims"])


def test_every_claim_has_reasons():
    doc = classify_document(DOC)
    assert all(c["reasons"] for c in doc["claims"])


if __name__ == "__main__":
    print("Claim Classifier tests (FACT/INFERENCE/UNKNOWN)")
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            test(fn)
    print(f"\n{PASS} passed, {FAIL} failed")
    raise SystemExit(1 if FAIL else 0)
