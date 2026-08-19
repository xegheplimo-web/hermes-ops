# WAVE 1 — REVISED sau Codex principal review (2026-08-21)

Reviewer: codex-cli 0.148.0, model gpt-5.6-sol, sandbox read-only.
Input: `.hermes/plans/STATUS-2026-08-21.md`

## Codex bác bỏ điều gì

1. Thứ tự cũ sai: audit/logger phải có TRƯỚC luồng nhận sự kiện, không phải sau.
2. **Không làm webhook receiver lúc này.** Dùng `gh api` polling trước.
   Lý do: webhook cần remote + secret + public ingress/tunnel. Unit test HMAC KHÔNG đủ để gọi là PASS.
3. Rủi ro chí tử: xây thêm package mà không có vertical slice E2E — đúng nguyên nhân sinh ra claim khống trước đây.

## Cắt khỏi P0 (theo Codex)

| Cắt | Lý do |
|---|---|
| `packages/webhook` | dùng gh polling/PAT trước |
| GitHub App | chưa cần |
| AgentMemory / iii-worker / WSL Ubuntu | không phục vụ slice |
| `packages/leader` (leader election) | 1 máy 1 operator |
| `packages/monitor` | SQL query + log là đủ |
| `packages/reconciliation` | thay bằng startup recovery job |
| CodeRabbit | Codex đã principal review; chưa chứng minh giá trị thêm |
| 3 tầng LOW/MED/HIGH/CRITICAL | chỉ cần `auto-eligible` / `human-required` |

## Giữ lại — LÕI

Postgres queue · audit evidence · deterministic tests · Codex review · human gate

## Thứ tự MỚI

| # | Việc | Ai | Gate |
|---|---|---|---|
| W1-3 | migration runner + schema_migrations checksum | Devin glm-5-2 | `\dt` liệt kê 6 bảng trên DB thật |
| W1-4 | integration test queue SKIP LOCKED, 2 worker | Devin glm-5-2 | 0 job claim trùng, DB thật |
| W1-5 | **audit + JSON logger** (đẩy lên trước) | Devin glm-5-2 | query `audit_events` thấy row thật |
| W1-6 | `gh` CLI + private GitHub remote + push | **CẦN SẾP** | `git push` thành công, `gh api` trả JSON |
| W1-7 | Codex review worker → ghi DB | Devin swe-1-7 | row có commit SHA + model + CLI version + verdict |
| W1-8 | **VERTICAL SLICE E2E** | Hermes điều phối | xem gate dưới |

## Gate của W1-8 — tiêu chuẩn duy nhất đáng tin

```
change thật
 → enqueue DB
 → worker claim (SKIP LOCKED)
 → agent sửa code
 → push branch + PR thật
 → deterministic tests
 → Codex verdict ghi DB
 → human merge
```

Bắt buộc lưu: command output, commit SHA, PR URL, DB evidence row.
**Nếu W1-8 chưa PASS: không được viết thêm bất kỳ package mới nào.**

## Ghi chú bảo mật

Không đưa `~/.codex/auth.json` vào GitHub Actions. OAuth local chỉ dùng cho worker chạy trên máy này, chưa verify refresh/expiry/failure-recovery.

## Vị trí Codex trong pipeline

```
agent tạo thay đổi
 → deterministic tests
 → Codex principal review (đọc diff tại commit SHA cố định)
 → policy gate
 → human approval
 → merge
```

Codex ghi DB: commit SHA, model + CLI version, prompt/policy version, test evidence, verdict, findings.

## Blocker đang chờ Sếp

1. Private GitHub remote cho `hermes-ops` — blocker TUYỆT ĐỐI cho mọi claim PR/evidence/required-check.
2. Xác nhận cắt danh sách trên khỏi P0.
