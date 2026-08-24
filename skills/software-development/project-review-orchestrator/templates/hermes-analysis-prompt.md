# Hermes Analysis Instructions

When performing the first-pass analysis before external review, structure your analysis with these 21 sections:

1. PROJECT SNAPSHOT — branch, commit, language breakdown, file counts
2. CURRENT IMPLEMENTED ARCHITECTURE — what exists
3. VERIFIED COMPLETED FEATURES — what is confirmed working
4. PARTIAL FEATURES — what is started but incomplete
5. BROKEN FEATURES — what is known broken
6. UNKNOWN AREAS — what you haven't inspected
7. CURRENT TEST/BUILD HEALTH — CI status, test counts, coverage gaps
8. SECURITY STATUS — auth, secrets, permissions
9. OPERATIONAL STATUS — deployment, monitoring, recovery
10. STATE MANAGEMENT — databases, caches, leases
11. CONCURRENCY — locks, races, parallelism
12. MEMORY BOUNDARIES — what AgentMemory owns vs Ops DB
13. EXTERNAL DEPENDENCIES — services, APIs, third-party
14. TECHNICAL DEBT — known shortcuts, migrations pending
15. ARCHITECTURE CONTRADICTIONS — code vs docs, multiple approaches
16. DUPLICATED RESPONSIBILITIES — overlapping modules
17. WRONG / STALE DOCUMENTATION — docs that mislead
18. MOST IMPORTANT RISKS — top 3-5
19. SIMPLER ALTERNATIVES — designs that could replace complex ones
20. RECOMMENDED PRIORITY ORDER — what to do first
21. QUESTIONS FOR INDEPENDENT REVIEWER — what you want challenged

Rules:
- Every non-trivial claim must have an evidence reference (file path, line, test name)
- OR be labeled: INFERENCE / UNKNOWN / UNVERIFIED
- Do NOT let the external review influence this analysis — it must be independent