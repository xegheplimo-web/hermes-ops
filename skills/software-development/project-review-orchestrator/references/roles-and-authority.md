# Roles & Authority — Hermes Canonical Lifecycle

Single source of truth for **who decides what**, **which model runs where**, and
**what each actor is forbidden to do**. Every script in this skill must be
readable against this document; if code and this file disagree, that is a bug.

## 0. The One-Brain Rule

> **Hermes decides. Everyone else produces evidence or performs work.**

There is exactly **one** orchestrator. No other actor may plan, re-plan,
re-prioritise, or promote its own findings into work. Any component that starts
making routing decisions has become a second orchestrator and must be reverted.

---

## 1. Authority tiers (who wins when sources disagree)

Ordered. Higher tier always wins.

| Tier | Authority | Meaning | Can it be overruled by an opinion? |
|-----:|-----------|---------|---|
| 1 | **Runtime reality** | What actually happened when executed | Never |
| 2 | **Tests / build / compiler** | Reproducible verification output | Never |
| 3 | **Current source code** | What the code says today | Never by prose |
| 4 | **Git / diff / PR** | Current implementation truth | No |
| 5 | **Ops DB** | Runtime truth: task state, lease, attempts | No |
| 6 | **Canonical docs** | Declared intent | Yes, by tiers 1–5 |
| 7 | **AgentMemory** | Verified historical context | Yes, by tiers 1–5 |
| 8 | **External reviewer findings** | Codex / CodeRabbit claims | Yes |
| 9 | **Agent opinions** | Anything unevidenced | Always |

**Rule:** a tier-8 or tier-9 claim never becomes work until Hermes reconciles it
against tiers 1–5.

---

## 2. Actor matrix

| Actor | Role | MAY | MUST NOT |
|---|---|---|---|
| **Hermes** | Brain / sole orchestrator | Analyse, detect conflicts, classify, route, reconcile, decide gates, promote lessons | Write production code itself; skip reconcile; let a reviewer decide |
| **Ops DB** | Runtime authority | Hold task DAG, status, lease, attempts, audit | Hold cognitive judgement or lessons |
| **Git/Repo** | Current implementation truth | Be the diff of record | Be inferred from memory |
| **AgentMemory** | Historical context | Store verified lessons, root causes, decisions | Store queue state, transient logs, secrets |
| **Strategy Router** | Policy helper (deterministic) | Map (task_type, risk) → strategy/gates/spec/attempts | Re-plan, re-prioritise, dispatch, override Hermes |
| **Codex** | Independent reviewer, **read-only** | Read repo, emit structured findings | Write files, push, approve merge, run mutating commands |
| **CodeRabbit** | PR reviewer | Comment on PRs | Merge, bypass gates |
| **Devin** | Sole implementer | Branch, code, test, PR, repair within scope | Exceed write_scope, self-merge, self-approve, redefine acceptance criteria |
| **Superpowers skills** | Discipline **under** DevinAdapter | Instruct *how* Devin works | Instruct Hermes; act as an agent |
| **Policy Gate** | Enforcement | Emit PASS / REPAIR / ESCALATE / BLOCK | Be bypassed by any agent |
| **Human (Sếp)** | Final authority on CRITICAL | Approve/deny, revoke, override anything | — |

---

## 3. Model assignment

### 3.1 Implementer (Devin)

Selected by **risk**, not by preference. Implemented in `dispatch_to_devin.py::RISK_MODEL_MAP`.

| Final risk | Model | Rationale |
|---|---|---|
| LOW | `glm-5-2` | Cheap, adequate for mechanical change |
| MEDIUM | `glm-5-2` | Default working model |
| HIGH | `swe-1-7` | Harder reasoning, sensitive surface |
| CRITICAL | `swe-1-7` + human gate | Never auto-merge |

Permission mode: headless `-p` requires `--permission-mode dangerous` to run
verification commands. `accept-edits` no-ops and `smart` is unavailable in `-p`.

### 3.2 Reviewer (Codex)

| Setting | Value | Why |
|---|---|---|
| Profile | `hermes-reviewer` | Isolated, auditable |
| Sandbox | `read-only` | Reviewer must never mutate the repo |
| Approvals | `never` / `approvals_reviewer=user` | No autonomous escalation |
| Model | `gpt-5.6-sol` (fallback `gpt-5.6`) | Independent critic |

Enforced by `scripts/test_codex_readonly.py` (isolation test).

### 3.3 Brain (Hermes)

Runs analysis, reconcile and gate decisions. No fixed pin required, but the
**reconcile step must not be delegated** to the reviewer or implementer.

---

## 4. Routing table (deterministic first)

`strategy_router.py` is rule-based. An LLM may only *add* detail; it may never
change `required_gates` or lower `max_attempts`.

| task_type | Strategy | Gates | Spec |
|---|---|---|---|
| BUG | systematic-debugging → regression-test → fix → verification | ci | none |
| FEATURE | spec → tdd → implement → verification | ci | lightweight/formal |
| SECURITY | threat-review → fix → security-verification | ci + codex | formal |
| REFACTOR | characterization-test → refactor → verification | ci | none |
| PERFORMANCE | benchmark → optimise → benchmark-verify | ci | lightweight |
| INFRA | validate → apply → verification | ci | lightweight |
| CONFIG | deterministic-validation → apply → verification | ci | none (**no forced TDD**) |
| MIGRATION | backup → migrate → verify-migration → rollback-plan | ci + codex | formal |
| INVESTIGATION | evidence-collection → report | ci | none |

Risk escalation on top of the table:

- HIGH → add `codex`
- CRITICAL → add `codex` + `human`

---

## 5. Gate semantics

Final risk is the **only** risk input to the gate. One contract:
`LOW | MEDIUM | HIGH | CRITICAL`.

| Condition | Outcome |
|---|---|
| All required gates green | **PASS** |
| CI red, attempts remaining | **REPAIR** |
| attempts ≥ max_attempts | **ESCALATE** |
| CRITICAL without human approval | **BLOCK** |
| Evidence stale / SHA mismatch | **BLOCK** |
| Risk downgraded without evidence | **BLOCK** |

Downgrade rule: risk may be lowered **only** with explicit evidence (all tests
pass, all findings resolved). Never lowered to bypass a reviewer.

---

## 6. Hard invariants

1. Real dispatch requires an Ops DB **lease** (`claim_task`, `FOR UPDATE SKIP LOCKED`).
2. `task-plan.json` is a **dry-run artifact only** — never a dispatch source.
3. `DISPATCHED` is written **only** when the implementer actually launched;
   otherwise `FAILED` with the error and attempt count.
4. Ops DB write failure ⇒ `PLAN_READY_NOT_DISPATCHED` and a non-zero exit.
5. `attempts < max_attempts` is enforced in the claim query (circuit breaker).
6. Reviewer findings enter work **only** through Hermes reconcile.
7. Repair loop is bounded (≤ 3), then ESCALATE.
8. AgentMemory receives **verified lessons only** — never queue state or secrets.
9. Secrets are redacted before any outbound packet; `[REDACTED]` in artifacts.
10. Strategy Router never dispatches and never re-plans.

---

## 7. Escalation path

```
Devin blocked / attempts exhausted   → Hermes
Hermes cannot resolve on evidence    → Codex (read-only opinion)
Still unresolved / CRITICAL          → Human (Sếp)
```

Only the human may waive an invariant, and the waiver is recorded as an audit
event with actor, timestamp, task_id, head_sha and risk.
