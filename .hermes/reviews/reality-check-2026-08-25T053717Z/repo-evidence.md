# Repository Evidence

## Snapshot
- Project: hermes-ops
- Branch: main
- Commit: `b4a92d86f10ab457e8107ade831d9be7123a83fc`
- Dirty: True
- Changed entries: 49
- Generated: 2026-08-24T22:37:17+00:00

## Files
- Tracked: 105
- Safe scanned: 104
- Test files: 17

## Languages
- TypeScript: 42
- Markdown: 25
- JSON: 23
- SQL: 5
- YAML: 4
- Python: 3
- Other: 1
- JavaScript: 1

## Manifests
- `docker-compose.yml`
- `package.json`
- `packages/adapters/package.json`
- `packages/contracts/package.json`
- `packages/db/package.json`
- `packages/gate/package.json`
- `packages/policy/package.json`
- `pnpm-lock.yaml`

## CI
- `.github/workflows/ci.yml`

## TODO Markers
```json
{
  "FIXME": 5,
  "HACK": 5,
  "TODO": 10,
  "XXX": 5
}
```

## Recent Commits
- `b4a92d86f1` 2026-08-23T05:43:10+07:00 — feat(cli): wire post-diff risk recalc, add --changed-files, --help; fix E2E smoke test schema
- `f04ceb031a` 2026-08-23T05:36:55+07:00 — ci: fix build — remove tracked tsbuildinfo, clean unused gate imports
- `8d8ad7b6bf` 2026-08-23T05:21:56+07:00 — ci: fix pnpm version conflict in action-setup (use packageManager field)
- `03d5ca48ad` 2026-08-23T05:17:32+07:00 — feat: complete P0 execution — human gate, risk classifier, post-diff risk, E2E slice, AgentMemory integration
- `f198aa2f2c` 2026-08-20T15:05:47+07:00 — feat(skills): add project-review-orchestrator skill
- `64e6bcda27` 2026-08-20T13:41:01+07:00 — ci: add GitHub Actions workflow + .env.example
- `d86bd55fa8` 2026-08-20T13:37:22+07:00 — feat(adapters): add DevinCliTransport — real CLI transport
- `d262314d43` 2026-08-20T02:33:32+07:00 — fix(db): stop writing NULL to NOT NULL available_at in claim SQL; add live-Postgres queue integration tests
- `f07e1f4a68` 2026-08-20T02:14:06+07:00 — feat(db): migration runner with sha256 checksum verification + schema_migrations bookkeeping
- `1f5d80eecd` 2026-08-20T01:55:20+07:00 — docs(plans): verified status report + Wave 1 revised after Codex principal review

## Top Churn — 90 Days
- `pnpm-lock.yaml`: 1172 (+1171/-1)
- `.hermes/plans/2026-08-20-agent-fleet-p0-completion.md`: 724 (+724/-0)
- `packages/adapters/tests/devin.test.ts`: 586 (+586/-0)
- `packages/gate/tests/cli.test.ts`: 522 (+504/-18)
- `skills/software-development/project-review-orchestrator/SKILL.md`: 520 (+520/-0)
- `packages/contracts/src/validation.ts`: 473 (+473/-0)
- `packages/gate/src/cli.ts`: 435 (+393/-42)
- `.hermes/reviews/20260820T223658Z_80ddaee/repo-evidence.json`: 408 (+408/-0)
- `.hermes/reviews/20260823T030000Z_80ddaee/repo-evidence.json`: 408 (+408/-0)
- `packages/db/tests/queue.integration.test.ts`: 400 (+400/-0)
- `packages/db/src/queue.ts`: 399 (+396/-3)
- `packages/adapters/src/devin.ts`: 388 (+388/-0)
- `packages/db/tests/migrate.test.ts`: 363 (+363/-0)
- `skills/software-development/project-review-orchestrator/scripts/collect_repo_evidence.py`: 329 (+329/-0)
- `packages/contracts/tests/manifest.test.ts`: 313 (+313/-0)
