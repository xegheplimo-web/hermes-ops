# Canonical Project Selection

## Selected Repository
| Field | Value |
|-------|-------|
| **Path** | `G:\Agent-Tools\hermes-ops` |
| **Branch** | `main` |
| **HEAD** | `80ddaee10393eca8f5552e226ebef3675ad0d976` |
| **Dirty** | true (5 untracked/built entries: `.pgdata/`, `__pycache__`, `tsconfig.tsbuildinfo`) |
| **Remote** | NONE — no GitHub remote configured yet |
| **Confidence** | **95%** |

## Why Selected

1. **Exact skill path match**: The design document specifies `skills/software-development/project-review-orchestrator/`. This path exists verbatim in hermes-ops.

2. **Scripts match**: The design specifies three scripts:
   - `scripts/collect_repo_evidence.py` — ✅ exists
   - `scripts/build_review_packet.py` — ✅ exists
   - `scripts/openai_review.py` — ✅ exists

3. **Templates match**: 
   - `templates/external-review-prompt.md` — ✅ exists
   - `templates/codemap-prompt.md` — ✅ exists

4. **References match**:
   - `references/governance.md` — ✅ exists
   - `references/review-contract.md` — ✅ exists
   - `references/codemap-contract.md` — ✅ exists

5. **SKILL.md content**: The SKILL.md's frontmatter (name, description, version, tags) exactly matches the design.

6. **Ops DB architecture**: `packages/db/` with queue primitives, migrations, schema.

7. **Policy evaluator**: `packages/policy/` with deterministic fail-closed evaluator.

8. **Adapters**: `packages/adapters/` with Devin, CodeRabbit, GitHub, coding-agent adapters.

9. **Policy gate CLI**: `packages/gate/` with `hermes-policy-gate`.

10. **Evidence contracts**: `packages/contracts/` with `EvidenceManifest`, validation, identity.

## Why Alternatives Were Rejected

| Candidate | Rejection Reason |
|-----------|-----------------|
| Understand-Anything | README explicitly says hermes-ops owns control plane. No skills/ops/gate. |
| agentmemory-main | Docker iii-engine only. No Hermes skills, no Ops DB, no policy. |
| FaceCraft-VN | Gradio Python project. No Hermes orchestration, no Ops DB, no skills. |

## Potential Unmerged Work Elsewhere
- **No unmerged work detected.** All Git repos with hermes-ops-related content point to the same origin.
- The previous 7-commit history of hermes-ops is fully contained in this repo.
- No worktrees, stashes, or orphaned branches with relevant content were found.

## Remaining Uncertainty
- The design document is titled "deep-research-report (1).md" — it's an analysis/design document, not current implementation instructions. The exact scope of "remaining work" must be derived from comparing design vs implementation, not from the design's own "next steps" sections.
- Whether AgentMemory integration should be pursued on Windows (iii-engine Docker exists but may have constraints) is undetermined.
- GitHub remote configuration requires human action (Sếp).