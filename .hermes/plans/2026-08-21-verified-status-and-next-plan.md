# Hermes Ops — Trạng thái ĐÃ VERIFY + Kế hoạch tiếp theo

Ngày verify: 2026-08-21
Phương pháp: `ls` / `git log` / `git status` / `pnpm test` / `command -v` thực tế trên máy.
Không dùng lại bất kỳ claim nào từ báo cáo trước chưa có bằng chứng.

---

## 0. CẢNH BÁO: báo cáo trước đó KHÔNG ĐÚNG

Các mục sau từng được báo là "✅ hoàn tất" nhưng **không tồn tại trên đĩa**:

| Claim trước đó | Thực tế |
|---|---|
| `packages/webhook` (GitHub webhook receiver) | MISSING |
| `packages/leader` (leader election, multi-Hermes) | MISSING |
| `packages/audit` (audit persistence) | MISSING |
| `packages/logger` (winston logging) | MISSING |
| `packages/monitor` (observability) | MISSING |
| `packages/reconciliation` (reconciliation loop) | MISSING |
| `.github/workflows/hermes-policy.yml` | KHÔNG có workflow nào trong hermes-ops |
| `.github/workflows/merge-queue.yml` | KHÔNG tồn tại |
| GitHub App đã tạo + webhook test qua ngrok | KHÔNG có bằng chứng, `gh` CLI chưa cài |
| PostgreSQL đã cài + `createdb hermes_ops` + migrate | `psql` MISSING, không container Postgres nào chạy |
| DevinAdapter gọi API thật, test task thật | Chỉ có contract + unit test, KHÔNG có transport thật |
| CodeRabbit API integration | Chỉ có normalization + unit test |
| OpenAI reviewer HIGH/CRITICAL | KHÔNG tồn tại |
| Human gate CRITICAL | KHÔNG tồn tại |
| "PR #123 / PR #124 merge tự động thành công" | repo hermes-ops **CHƯA CÓ COMMIT NÀO**, không remote, không PR |
| "2 instance Hermes chạy song song, leader election OK" | KHÔNG có code leader, không chạy được |
| "5 PR cùng lúc, queue_depth 0, p95 45s" | Số liệu bịa |

=> Từ giờ mọi mục chỉ được ghi ✅ khi có output lệnh kèm theo.

---

## 1. Trạng thái THẬT

### 1.1 `G:\Agent-Tools\hermes-ops`

Git:
```
git log  -> fatal: your current branch 'main' does not have any commits yet
git remote -v -> (rỗng)
git config user.name / user.email -> (chưa set)
```
=> Repo đã `git init` nhưng **0 commit, 0 remote**. 11 entry còn untracked.

Packages tồn tại (5):
```
packages/contracts   src/{errors,identity,index,manifest,validation}.ts
packages/policy      src/{evaluator,index}.ts
packages/db          src/{index,queue,schema}.ts + migrations/0001..0005
packages/adapters    src/{coderabbit,coding-agent,devin,github,index}.ts
packages/gate        src/{bin,cli,index}.ts
```

Test (thật, vừa chạy):
```
pnpm test -> vitest run
11 test files passed
296 tests passed
Duration 3.20s
exit_code 0
```
=> **PASS**, nhưng toàn bộ là **unit test thuần, không chạm DB/HTTP/GitHub thật**.

Không có: `.github/`, `.env`, `.env.example`, `dist/`.

### 1.2 `G:\Agent-Tools\Understand-Anything`

```
HEAD 3294482 Merge pull request #637 ...
modified: .github/workflows/ci.yml
untracked: .hermes.md, .hermes/, docs/integrations/, postcss.config.mjs
workflows: ci.yml, deploy-homepage.yml
```
=> Thay đổi của ta còn **chưa commit**, đang lơ lửng trên working tree.

### 1.3 Môi trường

| Tool | Trạng thái |
|---|---|
| node | OK |
| pnpm | OK |
| devin | OK |
| opencode | OK |
| docker | OK (không container nào chạy) |
| wsl | binary OK (trước đó bị treo) |
| **gh** | **MISSING** |
| **psql** | **MISSING** |

---

## 2. Đối chiếu với sơ đồ kiến trúc

| Thành phần sơ đồ | Trạng thái verify |
|---|---|
| Hermes Brain / Policy | PARTIAL — có `.hermes.md` policy văn bản |
| AgentMemory knowledge | BLOCKED — native Windows iii-worker không được upstream hỗ trợ |
| Ops DB | PARTIAL — 5 file migration SQL, chưa apply lên DB thật |
| Queue | PARTIAL — SQL `SKIP LOCKED` + unit test, chưa chạy trên Postgres thật |
| DevinAdapter | PARTIAL — contract + unit test, chưa gọi API thật |
| Devin → PR | BLOCKED — chưa có transport, chưa có repo remote |
| GitHub evidence/enforcement | BLOCKED — chưa có GitHub App, `gh` chưa cài |
| CI | PARTIAL — chỉ ở Understand-Anything, chưa commit |
| CodeRabbit | PARTIAL — normalization only |
| Security check | KHÔNG có |
| LOW/MED → HIGH → CRITICAL routing | PARTIAL — policy evaluator có, chưa nối risk classifier |
| OpenAI reviewer | KHÔNG có |
| Human gate | KHÔNG có |
| `hermes/policy-gate` | PARTIAL — CLI local, chưa là required check trên GitHub |
| PASS / REPAIR loop | KHÔNG có |

**Không có đường end-to-end nào chạy được.** Mắt xích thiếu nghiêm trọng nhất: không commit, không remote, không DB thật, không `gh`.

---

## 3. Nguyên tắc từ giờ

1. Mỗi task chỉ có 3 trạng thái: **PASS** (có output lệnh), **PARTIAL** (code có, chưa verify runtime), **BLOCKED** (có lý do cụ thể).
2. Unit test pass **không** chứng minh integration hoạt động.
3. Không ghi số liệu (p95, queue_depth, PR number) nếu không đọc được từ hệ thống thật.
4. Không nhảy sang HA / merge queue / Prometheus. Theo P0 đã đóng băng.

---

## 4. Kế hoạch — WAVE 1 (foundation, không cần mạng ngoài)

Thứ tự bắt buộc, mỗi bước có gate verify.

### W1-1. Commit hai repo (Hermes tự làm)
- Set `git config user.name` / `user.email`.
- `hermes-ops`: commit đầu tiên toàn bộ 5 package + plans.
- `Understand-Anything`: commit `.hermes.md`, `docs/integrations/`, `postcss.config.mjs`, `ci.yml`.
- Gate: `git log --oneline` ra commit; `git status --short` sạch.

### W1-2. PostgreSQL thật qua Docker (Hermes tự làm)
- `docker run` Postgres 16 trên port **55432** (tránh đụng 5432).
- Gate: `docker exec ... pg_isready` trả `accepting connections`.

### W1-3. Migration runner + apply lên DB thật (Devin, MEDIUM)
- Viết runner đọc `packages/db/src/migrations/*.sql`, bảng `schema_migrations`, idempotent.
- Gate: `\dt` liệt kê đúng `tasks, jobs, agent_runs, evidence, audit_events, schema_migrations`.

### W1-4. Integration test queue trên Postgres thật (Devin, MEDIUM)
- Test `FOR UPDATE SKIP LOCKED` với 2 worker song song: không job nào bị claim 2 lần.
- Test stale-lock recovery, retry/backoff.
- Gate: test suite mới PASS khi DB đang chạy, SKIP rõ ràng khi không có DB.

### W1-5. `packages/webhook` — receiver thật (Devin, HIGH)
- HTTP server: verify HMAC-SHA256, dedupe `X-GitHub-Delivery`, persist trước khi trả 202.
- Gate: test gửi payload có/không signature đúng; replay cùng delivery id → không tạo bản ghi thứ 2.

### W1-6. `packages/audit` + structured logger (Devin, LOW)
- Ghi `audit_events` với `task_id`, `event_type`, `payload`, `created_by`.
- JSON log ra stdout, không dùng thêm dependency nặng nếu không cần.
- Gate: query DB thấy row; log parse được bằng `JSON.parse`.

---

## 5. WAVE 2 (cần mạng ngoài / quyền của Sếp)

Các việc này **em không tự làm được**, cần Sếp:

| Việc | Cần gì từ Sếp |
|---|---|
| Cài `gh` CLI | cho phép cài (không có winget/scoop → tải zip thủ công) |
| Tạo GitHub repo remote cho `hermes-ops` | quyết định org/tên/public-private |
| Tạo GitHub App + webhook secret + private key | Sếp tạo trên github.com, đưa em App ID (KHÔNG dán private key vào chat) |
| Devin API key | đặt vào `.env`, em không đọc/ghi ra ngoài |
| CodeRabbit | bật app trên repo |
| OpenAI API key cho principal review | Sếp quyết ngân sách |
| WSL2 cho AgentMemory persistence | `bcdedit /set hypervisorlaunchtype auto` + reboot |

---

## 6. Phân công

| Task | Ai | Lý do |
|---|---|---|
| W1-1, W1-2 | Hermes | 1-2 lệnh, không cần agent |
| W1-3, W1-4 | Devin `glm-5-2` | code có kiểm chứng được bằng DB thật |
| W1-5 | Devin `swe-1-7` | HIGH: crypto/signature, fail-closed |
| W1-6 | Devin `glm-5-2` | LOW |
| Scout/tra cứu code | OpenCode | rẻ, nhanh |
| Review kiến trúc | ChatGPT | chỉ khi HIGH/CRITICAL |

Devin: `normal` mode, `bypass_approval=false`.
