# Governance Rules

## Authority Hierarchy

1. Runtime reality
2. Tests / compiler / build / actual execution
3. Current source code
4. Git / commit / diff / PR evidence
5. Ops DB authoritative runtime state
6. Canonical project documentation
7. AgentMemory verified historical context
8. External reviewer findings
9. Agent opinions

## External Reviewer Status

The external reviewer is a **critic**, not a decision-maker.

- The reviewer **never** directly modifies the repository.
- The reviewer **cannot** approve a merge.
- Hermes is the only authoritative project orchestrator.
- Reviewer findings are reconciled before any action is taken.

## Policy Gate

This skill **never** overrides `hermes/policy-gate`.

## Secret Handling

- Never include secrets in external review packets.
- Sanitize all outbound data through the Redaction Gate.
- AgentMemory is not a secret store.

## Human Gate

Human approval is required for:
- spending/transferring real money;
- production destructive operations;
- account deletion;
- credential rotation;
- CRITICAL security releases.
