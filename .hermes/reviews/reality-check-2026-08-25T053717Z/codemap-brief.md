# Codemap Brief

## Project
hermes-ops

## Snapshot Commit
b4a92d86f10ab457e8107ade831d9be7123a83fc

## Review Run
reality-check-2026-08-25T053717Z

## Task

Inspect the ACTUAL repository and create a code map.

Do not trust architecture documentation without verifying source.
Do not hallucinate files/functions.

For every major statement reference actual:
- path
- class
- function
- module
- entrypoint

## Map Scope

1. program entrypoints
2. major modules
3. request/control flow
4. state ownership
5. persistent storage
6. AgentMemory integration
7. Ops DB integration
8. Hermes orchestration
9. adapter boundaries
10. external integrations
11. subprocesses
12. concurrency
13. locks/leases
14. error propagation
15. retry flows
16. recovery flows
17. test architecture
18. CI path
19. security boundaries
20. modules implicated by accepted findings

## Accepted Reconciled Findings

- (no actionable findings)

## Questions Requiring Exact Repository Answers

## Questions Requiring Exact Repository Answers

(No high/critical priority findings identified.)

## Output

- CODEMAP
- KEY_FILES
- KEY_SYMBOLS
- CONTROL_FLOWS
- STATE_OWNERS
- DEPENDENCY_GRAPH
- FAILURE_PATHS
- TEST_MAPPING
- SECURITY_BOUNDARIES
- UNCERTAINTIES

Every uncertainty must be explicitly labeled.

DO NOT change source code.


## Implicated Modules

- (no module paths inferred)


## Repository-Specific Questions

1. What are every program's entrypoints and how is control handed off?
