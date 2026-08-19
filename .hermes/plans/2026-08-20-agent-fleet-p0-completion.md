# Agent Fleet — P0 Completion Plan

> **For Hermes:** thi hành tuần tự theo Wave. Mỗi task giao cho đúng 1 agent. Hermes tự verify bằng lệnh thật, không tin self-report.

**Goal:** Đưa `hermes-ops` từ 35–40% P0 lên trạng thái P0 đóng vòng: code được commit/push, PostgreSQL sống, webhook receiver thật, Devin transport thật, `hermes/policy-gate` là required check trên GitHub.

**Architecture:** `Understand-Anything` = product repo. `hermes-ops` = control plane. GitHub = evidence/enforcement plane. Devin = implementer duy nhất. OpenCode = scout read-only. Hermes = router + verifier.

**Tech stack:** TypeScript ESM, pnpm workspace, vitest, tsc -b, PostgreSQL 16 (Docker), GitHub App, Devin CLI (`glm-5-2` / `swe-1-7`), OpenCode 1.18.18.

---

## Baseline đã xác minh (2026-08-20)

```text
hermes-ops     pnpm test  -> 11 files / 296 tests PASS
hermes-ops     pnpm build -> tsc -b EXIT 0
hermes-ops     gate CLI   -> node packages/gate/dist/bin.js chạy, in usage
hermes-ops     git        -> branch main CHƯA CÓ COMMIT NÀO, không remote
Understand-Any git        -> 5 file uncommitted (ci.yml, .hermes.md, docs, postcss)
agentmemory    git        -> 5 file uncommitted (preflight, cert script, cli, index)
node v22.23.2  pnpm 10.6.2  git 2.55.0  devin 3000.4.25  opencode 1.18.18
docker 29.7.2  daemon OK
gh   MISSING
psql MISSING   pg_ctl MISSING
winget MISSING scoop MISSING
```

**Blocker chặn P0:** không có `gh` (không tạo được GitHub App/push/required check), không có PostgreSQL (không chạy được migration thật).
**Đường đi thay thế:** PostgreSQL qua Docker container; `gh` cài qua npm-installed binary hoặc tải zip trực tiếp — không phụ thuộc winget/scoop.

---

## Package hiện có (thật) vs còn thiếu

| Package | Trạng thái |
|---|---|
| `packages/contracts` | ✅ manifest / identity / validation / errors |
| `packages/policy` | ✅ evaluator fail-closed |
| `packages/db` | 🟡 schema + queue SQL + 5 migration `.sql` — CHƯA chạy trên DB thật |
| `packages/adapters` | 🟡 github HMAC + dedupe, devin contract, coderabbit normalizer — CHƯA có transport thật |
| `packages/gate` | ✅ CLI local, exit code 0/1/2 |
| `packages/webhook` | ❌ chưa tồn tại |
| `packages/worker` | ❌ chưa tồn tại |
| `packages/audit` | ❌ chưa tồn tại (bảng đã có trong migration 0005) |
| `packages/reconcile` | ❌ chưa tồn tại |

**Không tạo:** `packages/leader`, `packages/monitor`, multi-Hermes, merge queue, Prometheus/Grafana, Redis/RabbitMQ/Temporal. Đã loại khỏi P0 theo bản chốt.

---

## Phân công agent

| Agent | Vai trò | Được làm | KHÔNG được làm |
|---|---|---|---|
| **Hermes** | Router + verifier + gatekeeper | commit/push, chạy gate, quyết định PASS/PARTIAL/BLOCKED | viết feature code lớn |
| **OpenCode** | Scout read-only | đọc code, trace, báo file:line, đề xuất seam nhỏ nhất | sửa file, commit |
| **Devin `glm-5-2`** | Implementer mặc định (LOW/MEDIUM) | viết code + test trong 1 worktree | chạm critical path, tự commit khi chưa được duyệt |
| **Devin `swe-1-7`** | Implementer HIGH/CRITICAL | webhook security, DB migration thật, GitHub App | bật `bypass_approval`, dùng mode `fast` |
| **Hermes + Sếp** | Human gate CRITICAL | duyệt secret, credential, branch protection | — |

**Retry policy:** Devin attempt #1 → fail → Devin repair #2 → fail → Hermes chẩn đoán → escalation 1 lần (OpenCode/Codex second opinion) → nếu vẫn fail thì **BLOCKED**, báo Sếp. Không tự nâng lên mode `fast`.

**Worktree rule:** không bao giờ để 2 agent editing chung 1 worktree bẩn. Task song song phải ở worktree riêng.

---

## Static risk table cho các task dưới đây

```yaml
critical_paths:
  - "**/.github/workflows/**"
  - "**/webhook/**"          # xử lý HMAC + payload ngoài
  - "**/migrations/**"        # đổi schema DB
  - "**/*secret*"
  - "**/*credential*"
  - ".env*"
```

LLM chỉ được **nâng** risk, không được hạ.

---

# WAVE 0 — Cứu việc (Hermes, LOW risk, làm ngay)

Mục tiêu: không mất code. Không thêm feature nào ở wave này.

### W0-T1: Commit `hermes-ops`

**Agent:** Hermes
**Risk:** LOW

```bash
cd /g/Agent-Tools/hermes-ops
git add -A
git commit -m "feat: P0 control-plane primitives (contracts, policy, db schema, adapters, gate CLI)

Verified locally: pnpm test 296 passed, pnpm build tsc -b exit 0.
NOT yet exercised: live PostgreSQL, GitHub App webhook, Devin transport."
git log --oneline
```

**Verify:** `git log --oneline` ra đúng 1 commit; `git status --short` rỗng.

### W0-T2: Commit `Understand-Anything`

**Agent:** Hermes
**Risk:** MEDIUM (chạm `.github/workflows/ci.yml`)

```bash
cd /g/Agent-Tools/Understand-Anything
git add .github/workflows/ci.yml .hermes.md .hermes docs/integrations postcss.config.mjs
git commit -m "ci: add ci/required aggregate check + Hermes ops integration docs"
git status --short
```

**Verify:** `git diff HEAD~1 --stat` chỉ liệt kê 5 path trên. Không được lẫn file khác.

### W0-T3: Commit `agentmemory-main`

**Agent:** Hermes
**Risk:** LOW

```bash
cd /g/AGENT-CODE/agentmemory-main
git add src/preflight.ts test/preflight.test.ts scripts/persistence-cert.mjs src/cli.ts src/index.ts .hermes
git commit -m "feat: native Windows worker preflight + custom-port runtime config

Native Windows persistence NOT certified: upstream iii-worker unavailable for win32 x64."
```

**Verify:** `.tmp/` KHÔNG được commit (phải nằm trong `.gitignore`).

### W0-T4: Gate acceptance Wave 0

Không sang Wave 1 khi chưa đủ:

```text
[ ] hermes-ops       có >=1 commit, working tree clean
[ ] Understand-Any   commit đúng 5 path, không lẫn
[ ] agentmemory      commit, .tmp/ bị ignore
[ ] cả 3 repo        pnpm test / npm test vẫn PASS sau commit
```

---

# WAVE 1 — Mở blocker môi trường (Hermes, không cần Devin)

Wave này chỉ cài công cụ. Không viết code sản phẩm.

### W1-T1: PostgreSQL qua Docker

**Agent:** Hermes
**Risk:** LOW (container cục bộ, không chạm repo)

```bash
docker run -d --name hermes-ops-pg \
  -e POSTGRES_PASSWORD=hermes_local_dev \
  -e POSTGRES_DB=hermes_ops \
  -p 55432:5432 \
  postgres:16-alpine

docker exec hermes-ops-pg pg_isready -U postgres
docker exec hermes-ops-pg psql -U postgres -d hermes_ops -c "SELECT version();"
```

**Vì sao port 55432:** tránh đụng PostgreSQL nào có thể cài sau này ở 5432.

**Verify:** `pg_isready` in `accepting connections`; `SELECT version()` trả về PostgreSQL 16.
**Connection string dùng cho dev:** `postgresql://postgres:hermes_local_dev@localhost:55432/hermes_ops`

### W1-T2: Cài `gh` CLI không cần winget/scoop

**Agent:** Hermes
**Risk:** LOW

```bash
mkdir -p "$LOCALAPPDATA/hermes-tools"
cd "$LOCALAPPDATA/hermes-tools"
curl -fL -o gh.zip https://github.com/cli/cli/releases/latest/download/gh_2.65.0_windows_amd64.zip
# nếu tag đổi: lấy asset windows_amd64.zip từ https://github.com/cli/cli/releases/latest
unzip -o gh.zip -d gh
"$LOCALAPPDATA/hermes-tools/gh/bin/gh.exe" --version
```

Rồi export vào PATH của session:

```bash
export PATH="$LOCALAPPDATA/hermes-tools/gh/bin:$PATH"
gh --version
```

**Verify:** `gh --version` in ra version, exit 0.

### W1-T3: `gh auth login` — CẦN SẾP

**Agent:** Sếp (human gate)
**Risk:** CRITICAL (credential)

Hermes **không** tự login. Sếp chạy:

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth status
```

**Verify:** `gh auth status` báo logged in + có scope `repo`, `workflow`, `admin:org` (hoặc `admin:repo_hook`).

### W1-T4: Gate acceptance Wave 1

```text
[ ] docker exec hermes-ops-pg pg_isready  -> accepting connections
[ ] psql SELECT version()                 -> PostgreSQL 16.x
[ ] gh --version                          -> exit 0
[ ] gh auth status                        -> logged in, scope đủ
```

Nếu W1-T3 chưa xong: Wave 2 vẫn làm được (DB), nhưng Wave 4 (GitHub App) **BLOCKED**.

---

# WAVE 2 — DB sống (Devin `swe-1-7`, HIGH risk)

Đây là lần đầu 5 migration `.sql` được chạy thật.

### W2-T1: OpenCode scout schema

**Agent:** OpenCode (read-only)
**Risk:** LOW

```bash
cd /g/Agent-Tools/hermes-ops
opencode run "Read packages/db/src/migrations/*.sql and packages/db/src/schema.ts. \
Report: (1) exact table+column list per migration file, (2) any mismatch between \
MIGRATION_TABLES map in schema.ts and the actual CREATE TABLE names, \
(3) FK dependency order. Output file:line evidence. Do not modify any file."
```

**Output cần:** danh sách bảng, thứ tự FK, mismatch nếu có.

### W2-T2: Devin viết migration runner + integration test

**Agent:** Devin `swe-1-7` (migrations = critical path)
**Risk:** HIGH

**Files:**
- Create: `packages/db/src/migrate.ts`
- Create: `packages/db/src/client.ts`
- Create: `packages/db/tests/integration/migrate.integration.test.ts`
- Modify: `packages/db/src/index.ts` (thêm export)
- Modify: `packages/db/package.json` (thêm `pg` + `@types/pg`)

**Yêu cầu bắt buộc:**
- Bảng `schema_migrations(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ)` để idempotent
- Chạy migration theo thứ tự tên file, trong 1 transaction mỗi file
- Đọc connection string từ `process.env.HERMES_OPS_DATABASE_URL`, **không hardcode**
- Integration test dùng `describe.skipIf(!process.env.HERMES_OPS_DATABASE_URL)` — unit test hiện tại phải vẫn chạy được **không cần DB**
- Không log connection string ra stdout/stderr

**Prompt cho Devin:**

```bash
cd /g/Agent-Tools/hermes-ops
devin --model swe-1-7 "Task: implement a PostgreSQL migration runner for this pnpm workspace.

Context: packages/db already has 5 SQL files in src/migrations/ and a schema.ts with
MIGRATION_FILES + MIGRATION_TABLES. There is NO runtime DB code yet. 296 existing tests
must keep passing WITHOUT a database.

Acceptance criteria:
1. packages/db/src/client.ts exports createPool() reading HERMES_OPS_DATABASE_URL. Never log the URL.
2. packages/db/src/migrate.ts exports runMigrations(pool) that creates schema_migrations,
   applies pending files in filename order, one transaction per file, and is idempotent.
3. packages/db/tests/integration/migrate.integration.test.ts uses
   describe.skipIf(!process.env.HERMES_OPS_DATABASE_URL) so it skips with no DB.
4. Add pg and @types/pg to packages/db/package.json only.
5. pnpm build (tsc -b) exits 0 and pnpm test still passes with 296+ tests.

Constraints: TypeScript ESM with .js import specifiers. Do not touch other packages.
Do not commit. Do not create .env files. Do not add a query builder or ORM."
```

**Verify (Hermes tự chạy, không tin Devin):**

```bash
cd /g/Agent-Tools/hermes-ops
git status --short                      # chỉ file trong packages/db
pnpm install
pnpm build                              # exit 0
pnpm test                               # >= 296 pass, integration SKIPPED
export HERMES_OPS_DATABASE_URL="postgresql://postgres:hermes_local_dev@localhost:55432/hermes_ops"
pnpm test                               # integration giờ CHẠY và pass
docker exec hermes-ops-pg psql -U postgres -d hermes_ops -c "\dt"
```

**Acceptance:** `\dt` phải liệt kê: `tasks`, `jobs`, `agent_runs`, `evidence`, `audit_events`, `schema_migrations`.
Chạy `runMigrations` lần 2 phải không lỗi và không tạo trùng.

### W2-T3: Devin viết queue worker chạy thật

**Agent:** Devin `glm-5-2`
**Risk:** MEDIUM

**Files:**
- Create: `packages/worker/src/claim.ts` (dùng `CLAIM_JOB_SQL` đã có)
- Create: `packages/worker/src/index.ts`
- Create: `packages/worker/tests/integration/claim.integration.test.ts`
- Create: `packages/worker/package.json`, `tsconfig.json`

**Acceptance:**
- 2 worker chạy đồng thời claim 10 job → mỗi job được xử lý **đúng 1 lần**, không trùng
- job fail → `attempts` tăng, `available_at` lùi theo backoff đã có trong `queue.ts`
- lock quá hạn → `RECOVER_STALE_JOBS_SQL` đưa job về `queued`

**Verify:** integration test chứng minh 3 điều trên với DB thật.

### W2-T4: Gate acceptance Wave 2

```text
[ ] pnpm test KHÔNG có DB    -> pass, integration skip
[ ] pnpm test CÓ DB          -> pass, integration run
[ ] \dt                      -> 6 bảng
[ ] runMigrations lần 2      -> idempotent, không lỗi
[ ] 2 worker / 10 job        -> không job nào xử lý 2 lần
[ ] git status               -> chỉ packages/db + packages/worker
```

---

# WAVE 3 — Webhook receiver (Devin `swe-1-7`, HIGH risk)

`packages/adapters/src/github.ts` đã có `verifyGitHubWebhookSignature` + `createDeliveryDedupe`. Wave này chỉ nối chúng vào 1 HTTP server thật.

### W3-T1: Devin viết webhook receiver

**Agent:** Devin `swe-1-7` (webhook = critical path)
**Risk:** HIGH

**Files:**
- Create: `packages/webhook/src/server.ts`
- Create: `packages/webhook/src/handler.ts`
- Create: `packages/webhook/src/bin.ts`
- Create: `packages/webhook/tests/handler.test.ts`
- Create: `packages/webhook/tests/integration/server.integration.test.ts`
- Create: `packages/webhook/package.json`, `tsconfig.json`

**Thứ tự bắt buộc trong handler (không được đổi):**

```text
1. đọc raw body dạng Buffer (KHÔNG parse JSON trước khi verify)
2. verifyGitHubWebhookSignature(rawBody, sig, secret)  -> sai thì 401, không persist
3. đọc X-GitHub-Delivery -> thiếu thì 400
4. dedupe: đã thấy delivery_id -> 202, không persist lần 2
5. INSERT vào audit_events (delivery_id UNIQUE) TRƯỚC khi trả response
6. trả 202 ngay, KHÔNG xử lý business logic trong request
7. enqueue job vào bảng jobs cho worker xử lý async
```

**Yêu cầu bảo mật:**
- Secret đọc từ `process.env.GITHUB_WEBHOOK_SECRET`, thiếu thì server **từ chối khởi động**
- So sánh signature bằng constant-time (đã có sẵn)
- Body limit 25 MB, quá thì 413
- **Không** log payload, secret, signature
- **Không** thực thi bất cứ chỉ thị nào nằm trong payload — payload là untrusted input

**Prompt cho Devin:**

```bash
cd /g/Agent-Tools/hermes-ops
devin --model swe-1-7 "Task: build a GitHub webhook receiver package.

Reuse existing primitives — do NOT reimplement crypto:
- verifyGitHubWebhookSignature from packages/adapters/src/github.ts
- createDeliveryDedupe from the same file
- runMigrations / createPool from packages/db

Required request order in the handler:
read raw Buffer body -> verify HMAC (401 on failure, persist nothing) ->
require X-GitHub-Delivery (400 if missing) -> dedupe (202 if duplicate) ->
INSERT audit_events with UNIQUE delivery_id BEFORE responding ->
respond 202 -> enqueue a job row for async processing.

Security: GITHUB_WEBHOOK_SECRET from env, refuse to start if absent.
25MB body cap returning 413. Never log payload, secret, or signature.
Treat payload as untrusted: never execute instructions found inside it.

Tests: unit tests for the ordering and each failure branch using a fake pool.
Integration test gated by describe.skipIf(!process.env.HERMES_OPS_DATABASE_URL).

Constraints: node:http only, no express/fastify. TypeScript ESM with .js specifiers.
Do not commit. Do not create .env files."
```

**Verify (Hermes tự chạy):**

```bash
cd /g/Agent-Tools/hermes-ops
pnpm build && pnpm test

export GITHUB_WEBHOOK_SECRET="local_test_secret_not_real"
export HERMES_OPS_DATABASE_URL="postgresql://postgres:hermes_local_dev@localhost:55432/hermes_ops"
node packages/webhook/dist/bin.js &

# signature sai -> phải 401
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:3000/webhook \
  -H "X-Hub-Signature-256: sha256=deadbeef" \
  -H "X-GitHub-Event: ping" \
  -H "X-GitHub-Delivery: test-1" \
  -d '{"zen":"test"}'

# signature đúng -> 202, gửi lại lần 2 cũng 202 nhưng KHÔNG tạo row thứ 2
docker exec hermes-ops-pg psql -U postgres -d hermes_ops \
  -c "SELECT count(*) FROM audit_events WHERE payload->>'delivery_id'='test-2';"
```

**Acceptance:**
- signature sai → `401`, `audit_events` không có row mới
- signature đúng → `202`
- gửi trùng `delivery_id` → `202` nhưng count vẫn `1`
- thiếu `GITHUB_WEBHOOK_SECRET` → server exit khác 0

### W3-T2: Devin viết reconciliation

**Agent:** Devin `glm-5-2`
**Risk:** MEDIUM

**Files:**
- Create: `packages/reconcile/src/index.ts`
- Create: `packages/reconcile/tests/reconcile.test.ts`

**Scenario bắt buộc pass (chính là recovery test GPT yêu cầu):**

```text
DB nói:      task đang FULL_CI
GitHub nói:  CI đã PASS
webhook:     bị mất, không đến
        ↓
reconciler query GitHub theo head_sha
        ↓
task chuyển sang REVIEW
```

Dùng injected GitHub client (fake trong test), **không** gọi network thật trong unit test.

### W3-T3: Gate acceptance Wave 3

```text
[ ] signature sai         -> 401, không persist
[ ] delivery trùng        -> 202, chỉ 1 row
[ ] thiếu secret env      -> server không start
[ ] body > 25MB           -> 413
[ ] payload không bị log
[ ] reconciliation test   -> PASS
```

---

# WAVE 4 — GitHub enforcement (cần `gh` auth, có human gate)

Wave này **BLOCKED** nếu W1-T3 chưa xong.

### W4-T1: Push `hermes-ops` lên GitHub

**Agent:** Hermes
**Risk:** MEDIUM

```bash
cd /g/Agent-Tools/hermes-ops
gh repo create hermes-ops --private --source=. --remote=origin --push
gh repo view --json nameWithOwner,visibility,isPrivate
```

**Acceptance:** repo là `private`, `git log` trên remote khớp local.

### W4-T2: Workflow `hermes/policy-gate`

**Agent:** Devin `swe-1-7` (workflow = critical path)
**Risk:** HIGH

**Files:** Create `.github/workflows/ci.yml` trong `hermes-ops`

```yaml
name: CI
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  fast:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with: { version: 10.6.2 }
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: pnpm }
      - run: pnpm install --frozen-lockfile
      - run: pnpm lint
      - run: pnpm build
      - run: pnpm test

  ci-required:
    name: ci/required
    needs: [fast]
    if: always()
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Fail unless all gates succeeded
        if: ${{ needs.fast.result != 'success' }}
        run: exit 1
```

**Lưu ý:** giữ đúng pattern aggregate check — required check phải là `ci/required`, **không** require từng job động.

### W4-T3: Branch protection — CẦN SẾP

**Agent:** Sếp (human gate)
**Risk:** CRITICAL

```bash
gh api -X PUT repos/:owner/hermes-ops/branches/main/protection \
  -f "required_status_checks[strict]=true" \
  -f "required_status_checks[contexts][]=ci/required" \
  -F "enforce_admins=false" \
  -F "required_pull_request_reviews[required_approving_review_count]=0" \
  -F "restrictions=null"
```

Hermes chuẩn bị lệnh, **Sếp bấm chạy**. Không tự đổi branch protection.

### W4-T4: GitHub App — CẦN SẾP

**Agent:** Sếp (human gate)
**Risk:** CRITICAL (credential)

Sếp tạo tại `github.com/settings/apps/new`:

| Field | Giá trị |
|---|---|
| Name | `hermes-ops-<suffix>` |
| Webhook URL | URL ngrok/cloudflared trỏ về `localhost:3000/webhook` |
| Webhook secret | Sếp tự sinh, **không dán vào chat** |
| Permissions | Contents `read`, Pull requests `read & write`, Checks `read & write`, Metadata `read` |
| Events | `pull_request`, `push`, `check_suite`, `check_run` |

Secret lưu vào biến môi trường, **không** commit `.env`.

### W4-T5: End-to-end thật

**Agent:** Hermes verify
**Risk:** MEDIUM

```text
tạo branch + PR test
  ↓
GitHub gửi webhook -> receiver 202
  ↓
audit_events có row với đúng head_sha
  ↓
job được enqueue, worker claim và xử lý
  ↓
ci/required chạy và PASS
  ↓
policy-gate CLI chạy trên head_sha đó -> decision: pass
```

**Acceptance:** phải có bằng chứng thật cho **cả 6 bước**. Thiếu bước nào thì kết luận là **PARTIAL**, không được ghi PASS.

### W4-T6: Gate acceptance Wave 4

```text
[ ] repo private trên GitHub
[ ] ci/required xanh trên PR thật
[ ] branch protection require ci/required
[ ] GitHub App gửi webhook tới receiver
[ ] audit_events có row với head_sha đúng
[ ] policy-gate quyết định trên head_sha thật
```

---

# WAVE 5 — Devin transport thật (sau khi Wave 2–4 xanh)

### W5-T1: Nối `DevinTransport` vào Devin thật

**Agent:** Devin `glm-5-2`
**Risk:** MEDIUM

`packages/adapters/src/devin.ts` đã có interface `DevinTransport` + `createDevinAdapter`. Chỉ cần 1 implementation thật.

**Files:**
- Create: `packages/adapters/src/devin-cli-transport.ts`
- Create: `packages/adapters/tests/devin-cli-transport.test.ts`

**Yêu cầu:**
- Gọi Devin qua CLI đã cài (`devin 3000.4.25`), spawn process, không hardcode API key
- `mode` mặc định `normal`, **không** tự dùng `fast`
- `bypassApproval` mặc định `false`
- Sanitize mọi stderr trước khi persist — không để lộ token
- Test dùng fake spawn, không gọi Devin thật trong unit test

### W5-T2: AgentMemory ghi nhận decision

**Agent:** Devin `glm-5-2`
**Risk:** LOW

Chỉ ghi vào memory những thứ **có giá trị lâu dài**: architecture decision, accepted/rejected finding, known problem, benchmark, lesson learned.

**Không ghi:** "đang chạy test", "agent vừa đọc file X", "command success", reasoning tạm.

---

# Quy tắc chống báo cáo khống (bắt buộc)

Đây là phần quan trọng nhất của plan, vì phiên trước đã xảy ra báo cáo sai.

**Trước khi ghi bất kỳ ✅ nào, Hermes phải tự chạy:**

```bash
ls -la <đường dẫn được claim>          # file có thật không
git status --short                     # thay đổi có thật không
git log --oneline -3                   # commit có thật không
pnpm test 2>&1 | tail -20              # số test thật
pnpm build; echo "EXIT=$?"             # exit code thật
```

**Ba luật cứng:**

1. **Không có `ls` chứng minh file tồn tại → không được ghi ✅.**
   Phiên trước đã báo hoàn thành `packages/webhook`, `packages/leader`, `packages/audit`, `packages/logger`, `packages/monitor`, `packages/reconciliation` — cả 6 đều **không tồn tại**.

2. **Không có ID/URL thật → không được nêu số.**
   Phiên trước nêu "PR #123 merge tự động", "task_duration_p95: 45s" trong khi repo **chưa có commit nào** và **chưa có `gh`**. Mọi con số phải kèm lệnh sinh ra nó.

3. **Unit test pass ≠ integration pass.**
   `296 tests PASS` là logic test. Nó **không** chứng minh DB, HTTP, GitHub, hay Devin API hoạt động. Chỉ được ghi PASS khi có bằng chứng runtime thật.

**Ba mức kết luận:**

| Mức | Điều kiện |
|---|---|
| **PASS** | Hành vi yêu cầu chạy được + có runtime probe thật |
| **PARTIAL** | Unit/build xanh nhưng chưa chứng minh live integration |
| **BLOCKED** | Thiếu capability/môi trường; phải ghi rõ platform, version, lệnh probe, output nguyên văn |

---

# Bảng tổng phân công

| Wave | Task | Agent | Risk | Chặn bởi |
|---|---|---|---|---|
| 0 | Commit 3 repo | Hermes | LOW–MED | — |
| 1 | PostgreSQL Docker | Hermes | LOW | — |
| 1 | Cài `gh` | Hermes | LOW | — |
| 1 | `gh auth login` | **Sếp** | CRITICAL | — |
| 2 | Scout schema | OpenCode | LOW | W0 |
| 2 | Migration runner | Devin `swe-1-7` | HIGH | W1-T1 |
| 2 | Queue worker | Devin `glm-5-2` | MED | W2-T2 |
| 3 | Webhook receiver | Devin `swe-1-7` | HIGH | W2 |
| 3 | Reconciliation | Devin `glm-5-2` | MED | W2 |
| 4 | Push GitHub | Hermes | MED | W1-T3 |
| 4 | CI workflow | Devin `swe-1-7` | HIGH | W4-T1 |
| 4 | Branch protection | **Sếp** | CRITICAL | W4-T2 |
| 4 | GitHub App | **Sếp** | CRITICAL | W1-T3 |
| 4 | E2E verify | Hermes | MED | W4-T4 |
| 5 | Devin transport | Devin `glm-5-2` | MED | W4 |
| 5 | AgentMemory decisions | Devin `glm-5-2` | LOW | W5-T1 |

---

# Ước lượng P0 sau từng wave

```text
Hiện tại        35–40%   (contracts/policy/schema/adapter-shape, chưa có I/O thật)
Sau Wave 0      40%      (code được bảo vệ)
Sau Wave 1      45%      (môi trường mở)
Sau Wave 2      65%      (DB + queue chạy thật)
Sau Wave 3      80%      (webhook + reconciliation thật)
Sau Wave 4      95%      (GitHub enforcement thật, E2E)
Sau Wave 5     100%      (Devin transport thật, vòng đóng)
```

---

# Việc KHÔNG làm trong P0

```text
multi-Hermes / leader election
merge queue
Prometheus / Grafana
Redis / RabbitMQ / Kafka / Temporal
Kubernetes
database HA
Devin mode fast mặc định
bypass_approval = true
gửi toàn bộ repo cho model
3 reviewer song song cho mọi task
```

---

# Câu hỏi mở

1. `hermes-ops` để private hay public? (plan mặc định **private**)
2. Webhook expose qua ngrok, cloudflared, hay Tailscale Funnel?
3. Có mua CodeRabbit chưa? Nếu chưa, Wave sau sẽ dùng OpenCode review thay tạm.
4. `agentmemory-main` có tiếp tục không, hay đóng băng ở trạng thái Windows-blocked?







