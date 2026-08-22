# Task Execution DAG

## Summary
| ID | Title | Risk | Deps | Executor | Status |
|----|-------|------|------|----------|--------|
| HERMES-001 | Start Docker + apply DB migrations | MEDIUM | none | Hermes | PLANNED |
| HERMES-002 | Run integration tests against live DB | MEDIUM | HERMES-001 | Hermes | PLANNED |
| HERMES-003 | Implement human approval gate | HIGH | none | Devin | PLANNED |
| HERMES-004 | Implement risk classifier routing | MEDIUM | none | Devin | PLANNED |
| HERMES-005 | Implement post-diff risk recalculation | MEDIUM | HERMES-004 | Devin | PLANNED |
| HERMES-006 | E2E vertical slice smoke test | HIGH | HERMES-001/002/003/005 | Hermes+Devin | PLANNED |

## DAG
```
HERMES-001 (docker+migrate)    HERMES-003 (human gate)    HERMES-004 (risk class)
       |                                                        |
       v                                                        v
HERMES-002 (integration tests)                          HERMES-005 (post-diff risk)
       |                                                        |
       +---------------------------+----------------------------+
                                   |
                                   v
                            HERMES-006 (E2E slice)
```

## Parallel Execution
- **Group A**: HERMES-001 (Docker) — singleton, can't parallelize Docker startup
- **Group B**: HERMES-003, HERMES-004 — independent, parallel-safe (different packages)
- **Group C**: HERMES-002 (depends on A), HERMES-005 (depends on B) — sequential within group
- **Group D**: HERMES-006 (depends on A+B+C) — final integration

## Outside DAG (blocked on Sếp)
- GitHub remote configuration (blocker for PR/CI/GitHub evidence)