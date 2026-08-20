# Codemap Contract

## What Codemap Is

- A **navigation aid** for repository architecture.
- A **visual map** of code flow, module relationships, state ownership.
- A **context source** for implementation planning.

## What Codemap Is NOT

- A **source of truth** — verify against actual code and tests.
- A **merge authority** — it cannot approve changes.
- A **substitute** for reading code — it orients, not replaces.

## Usage in Workflow

1. Generate **after** reconciliation (not before).
2. Use the **reconciled review** as input, not raw external review.
3. Verify critical claims against source.
4. Use for **context** when creating implementation tasks.

## Snapshot Binding

A Codemap MUST reference the exact commit SHA used for evidence.
If the repository moves, the Codemap is **invalidated** and must be regenerated.

## Integration Points

- Codemap findings feed into **task DAG** creation.
- Codemap context is sent to **DevinAdapter** in implementation contracts.
- Codemap is **not** stored in AgentMemory — it lives in `.hermes/reviews/<RUN_ID>/`.
