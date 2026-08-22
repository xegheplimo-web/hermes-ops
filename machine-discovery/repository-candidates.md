# Repository Candidates

## Discovery Method
- Searched all development drives (A, C, D, G) for `.git` directories
- Bounded metadata-first scanning (up to depth 5)
- Filtered out: system directories, package caches, credential stores, browser profiles, Docker volumes

## Candidates Found

### REPO-A: `G:\Agent-Tools\hermes-ops` ← PRIMARY CANDIDATE
| Property | Value |
|----------|-------|
| Path | `G:\Agent-Tools\hermes-ops` |
| Branch | `main` |
| HEAD | `80ddaee10` |
| Dirty | 5 changed (all untracked: .pgdata/, pycache/) |
| Remote | NONE |
| Files | 79 tracked |
| Tests | 310 pass / 5 skip (integration needs live DB) |
| Build | ✅ |
| Packages | contracts, policy, db, adapters, gate |
| Skills | ✅ project-review-orchestrator at `skills/software-development/` |
| .hermes | ✅ plans + reviews |
| GitHub CI | ✅ .github/workflows/ci.yml |
| Design match | HIGH — matches every architecture requirement |

### REPO-B: `G:\Agent-Tools\Understand-Anything`
- Sibling repo. README explicitly separates it: hermes-ops owns control plane, Understand-Anything owns source analysis.
- Not the target of this design document.

### REPO-C: `G:\Agent-Tools\agentmemory-main`
- Docker-based iii-engine for AgentMemory. Referenced in design as potential backend.
- Not the project itself.

### REPO-D: `D:\FaceCraft-VN`
- Gradio desktop app for AI face editing.
- No Hermes orchestration, no Ops DB, no skills directory.
- Different project entirely.

## Conclusion
Only **REPO-A (hermes-ops)** matches the design document. No other candidate has:
- Hermes skill directory with project-review-orchestrator
- Ops DB queue primitives
- Policy evaluator
- Adapter architecture
- Evidence/review infrastructure