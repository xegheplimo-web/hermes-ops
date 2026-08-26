# Agent Role Matrix

This document defines the authority model for governed Hermes project execution.

## Actors

| Actor | Role | Allowed | Forbidden |
|---|---|---|---|
| USER | Principal | Request work, approve critical actions, inspect artifacts | Bypass policy-gate through informal instruction |
| HERMES | Orchestrator | Plan, reconcile evidence, route tasks, invoke skills, prepare review packets, coordinate agents | Bypass policy-gate, treat AgentMemory as runtime truth, let external reviewer implement code |
| AgentMemory | Cognitive memory | Provide historical decisions, lessons, incidents; store verified durable lessons | Own task queue, own lease state, override repository evidence |
| Ops DB | Runtime truth | Store task state, queue state, leases, attempts | Replace Git evidence, replace policy-gate |
| Evidence Collector | Deterministic evidence | Read repository metadata, count files, collect Git state, collect manifests/tests/CI stats | Modify repository, export secrets |
| Redaction Gate | Security boundary | Block secret export, redact sensitive patterns, require manual review when uncertain | Send raw secrets externally |
| OpenAIReviewAdapter | External review signal | Receive sanitized packet, produce structured critique | Modify repository, create tasks directly, approve merges, access secrets |
| ChatGPT Human Mode | Manual external review | Human-visible critique, manual copy/export by user | Programmatic scraping of ChatGPT consumer output |
| Devin Codemap / Ask Devin | Architecture navigation | Map code flows, identify candidate files, support planning | Act as final proof, modify repository |
| DevinAdapter | Implementation gateway | Dispatch approved tasks to Devin, track Devin sessions | Bypass Ops DB, bypass policy-gate |
| Devin | Implementation executor | Implement assigned tasks, create branch/worktree, open PR | Merge without gates, change policy, bypass DevinAdapter |
| GitHub | Change control plane | Host branches, PRs, record review history | Be bypassed by direct repo mutation |
| CI | Deterministic evidence | Run tests, produce build/test evidence | Approve policy exceptions |
| Security Scanners | Deterministic evidence | Produce security findings | Expose secrets in reports |
| CodeRabbit | Review signal | Comment on PR, suggest changes | Merge autonomously, override policy-gate |
| hermes/policy-gate | Enforcement | Pass/fail changes, require repair, require human approval | Be bypassed by any agent |
| Human | Critical authority | Approve critical changes, resolve conflicts, grant exceptions | Be silently bypassed for critical actions |

## Routing Rules

### Route A — Trivial low-risk change
- Scope: tiny, no security/auth/architecture change, clear verification
- Flow: Hermes → branch/worktree → DevinAdapter (if needed) → PR → CI + Security → policy-gate

### Route B — Standard governed implementation
- Scope: normal feature work, bounded risk
- Flow: /project-agent-bootstrapper → /project-review-orchestrator → full audit pipeline → PR/CI/gate

### Route C — High-risk or critical change
- Scope: auth, secrets, billing, migration, production, data integrity, policy-gate, orchestrator
- Flow: Full audit + external review + Codemap + human approval (for CRITICAL) → PR/CI/gate

## Hard Rules

1. No execution task without evidence_refs.
2. No implementation before external review when policy requires it.
3. No external reviewer directly changes repository.
4. No AgentMemory record is authoritative runtime state.
5. No task is marked DONE without verification evidence.
6. No HIGH task passes without required independent review.
7. No CRITICAL task passes without human approval.
8. No PR may bypass CI/security requirements.
9. No external packet may knowingly contain secrets.
10. Review commit SHA must match code snapshot or drift must be reconciled.
