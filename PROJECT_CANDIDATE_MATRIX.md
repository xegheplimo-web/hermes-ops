# Project Candidate Matrix

| Candidate | Path | Git? | Branch | HEAD | Dirty | Remote | Last Commit | Arch Match | Impl Match | Activity | Confidence | Reasons | Contradictions |
|-----------|------|------|--------|------|-------|--------|-------------|------------|------------|----------|------------|---------|----------------|
| **hermes-ops** | G:\Agent-Tools\hermes-ops | ✅ | main | 80ddaee10 | 5 untracked | NONE | 2026-08-20 | HIGH | HIGH | HIGH | **95%** | Skills dir, Ops DB, policy gate, adapters, review evidence, design-specified scripts | No remote (blocker but expected) |
| Understand-Anything | G:\Agent-Tools\Understand-Anything | ✅ | — | — | — | — | — | LOW | LOW | MEDIUM | 15% | Sibling repo in Agent-Tools | README explicitly separates concerns |
| agentmemory-main | G:\Agent-Tools\agentmemory-main | ✅ | — | — | — | — | — | LOW | LOW | MEDIUM | 10% | Referenced as dep, not target | No Ops DB, no skills, no hermes config |
| FaceCraft-VN | D:\FaceCraft-VN | ✅ | feature/smart-edit | e3d910a9 | 12 | NONE | recent | NONE | NONE | HIGH (own project) | 5% | Different tech stack (Gradio/Python) | No Hermes integration, no Ops DB, no policy |

## Scoring Legend
- **Architecture Match**: How well the repo structure matches the design's architecture (Hermes brain, Ops DB, AgentMemory, DevinAdapter, policy gate, skills)
- **Implementation Match**: How much of the designed implementation exists in source
- **Activity Score**: Recency and volume of development
- **Confidence**: Overall confidence this is the canonical project for this design

## Verdict
**`G:\Agent-Tools\hermes-ops`** is the only repository that:
1. Contains a `skills/software-development/project-review-orchestrator/` directory matching the exact path in the design
2. Has Ops DB queue primitives (packages/db with FOR UPDATE SKIP LOCKED)
3. Has deterministic policy evaluator (packages/policy)
4. Has adapters for Devin/CodeRabbit/GitHub (packages/adapters)
5. Has policy gate CLI (packages/gate)
6. Has evidence contract types (packages/contracts)
7. Has .hermes/ with reviews and plans
8. Has the exact scripts (collect_repo_evidence.py, build_review_packet.py, openai_review.py) described in the design
9. Has templates (external-review-prompt.md, codemap-prompt.md) described in the design
10. Has references (governance.md, review-contract.md, codemap-contract.md) described in the design

No other candidate has 2+ of these criteria.