# Hermes First-Pass Analysis — FaceCraft-VN

## PROJECT SNAPSHOT

- **Repo**: `D:\FaceCraft-VN`
- **Branch**: `feature/smart-edit`
- **HEAD**: `2cb7964b` (dirty)
- **Dirty files**: `engines/qwen-edit/edit_worker.py`, `tests/test_smart_edit_worker.py`, `?? .hermes/reviews/run_20260823_01/prompts/T01-fix-quantization.md`
- **Tracked files**: 175 (Python 89, Markdown 70, YAML 2, JSON 1)
- **Tests**: 34 files, 368 passed/2 skipped locally, CI runs on `ubuntu-latest` with `not gpu` marker
- **TODO/FIXME**: 48 / 2
- **CI**: `.github/workflows/ci.yml` (ruff + bandit + unit tests)

## 1. EXECUTIVE SUMMARY

FaceCraft-VN là desktop app Windows/NVIDIA cho face swapping + smart image editing, kiến trúc 3 tầng UI/Service/Engine, GPU singleton cross-process, input validation đầy đủ. Codebase đã có test suite khủng (368 tests pass) và nhiều security guard đúng hướng. Tuy nhiên, một số capability quan trọng trong design doc vẫn chưa triển khai (RBAC, PocketBase, billing/usage, rate limiting). Qwen NF4 4-bit fix đang nằm ở dirty `edit_worker.py`, chưa verify trên GPU. SAM2 checkpoint loading phụ thuộc `torch.load` pickle từ thư viện bên ngoài, cần giảm thiểu rủi ro.

## 2. ARCHITECTURE UNDERSTOOD

- UI: Gradio tabs (Ảnh/Video/SmartEdit/Qwen/Account).
- Services: adapters gọi worker process qua `subprocess.run(..., shell=False)` với stdin JSON.
- GPU Resource Manager: in-process `BoundedSemaphore(1)` + cross-process `FileLock`.
- Engines: FaceFusion 3.8.2 (subprocess), Smart Edit (Florence-2 → SAM2 → LaMa/SDXL), Qwen Edit (Qwen3-1.7B NF4).
- Models: manifest hash verification, model pinning.
- Input validator: path traversal, UNC, reserved device names, decompression bomb, control chars.

## 3. CURRENT STRENGTHS

1. **Separation of concerns**: UI/Service/Engine rõ ràng, worker process cô lập.
2. **Security guards**: `shell=False`, input validator, no raw CLI strings from user, safe subprocess.
3. **Test coverage**: 34 test files, mock/stub GPU dependencies; CI phân tách `not gpu`.
4. **GPU safety**: singleton + filelock đảm bảo 1 engine dùng GPU.
5. **Model integrity**: manifest + hash, pin by commit.
6. **Dirty diff `edit_worker.py`**: sửa `PipelineQuantizationConfig` cho `diffusers>=0.34`, đúng API signature theo tài liệu diffusers.

## 4. CURRENT WEAKNESSES / MISSING EVIDENCE

1. **RBAC / Authorization**: design doc `AUTHORIZATION_ANALYSIS_*` tồn tại nhưng chưa thấy code thực thi trong `app/`. App chạy local không giới hạn.
2. **PocketBase backend**: chỉ có trong analysis, chưa integrate.
3. **Usage / Billing / Credits**: không có implementation.
4. **Rate limiting**: không có; nếu expose API endpoint sẽ dễ bị spam.
5. **Qwen NF4 GPU verify**: dirty code chưa commit, chưa chạy thực tế trên GPU.
6. **SAM2 on Windows + pickle risk**: README khuyên WSL, project dùng native Windows; SAM2 `.pt` checkpoint load qua `build_sam2` gọi `torch.load` (bên ngoài project).
7. **Docker/WSL**: không dùng, native Windows + `.venv`.
8. **CI ignores nhiều test files**: `.github/workflows/ci.yml` ignore hầu hết engine tests.
9. **Lint issues**: ruff báo 7 lỗi nhỏ (unused import, import sort, shadow variable).
10. **Gradio `theme` deprecation warning**: `app/ui/main_ui.py:499` dùng `theme` trong `Blocks`.

## 5. CONTRADICTIONS

- Analysis đề xuất phân quyền nhưng code chưa có → app local chạy không giới hạn.
- Qwen NF4 fix đang dirty (chưa commit) nhưng tests pass (không load pipeline thực).
- CI bỏ qua `test_smart_edit_worker.py`, `test_qwen_edit_gpu.py`, etc. → unit tests green nhưng E2E engine chưa covered.

## 6. TECHNICAL DEBT

- 48 TODO markers.
- `Gradio.Blocks(theme=...)` sẽ bị xóa ở Gradio 6.0.
- Multiple agent skill dirs (`.agents/`, `.codex/`, `.devin/`, `.windsurf/`, `.opencode/`) duplicate skills, gây churn.

## 7. SECURITY RISKS

| ID | Risk | Severity | Evidence |
|---|---|---|---|
| SEC-01 | SAM2 checkpoint pickle (`torch.load` in `build_sam2`) | MEDIUM/HIGH | `engines/smart-edit/executors/sam2_mask.py:54` loads `.pt` via third-party SAM2 lib |
| SEC-02 | No rate limiting / auth on Gradio if exposed | MEDIUM | `app.py`/`main_ui.py` không có middleware auth/rate limit |
| SEC-03 | Missing RBAC/authorization despite analysis | MEDIUM | `AUTHORIZATION_ANALYSIS_2026-08-20.md` tồn tại nhưng `app/` không implement |

## 8. OPERATIONAL RISKS

- Qwen `.venv-qwen` symlink/breakage risk (AGENTS.md ghi).
- Port 7860 leak on kill.
- Windows-only; không containerization.

## 9. MISSING EVIDENCE

- VRAM log thực tế cho Qwen NF4.
- E2E UI→service→engine flow trên GPU.
- Bandit/ruff output gần nhất trong repo (output lưu `.json` cũ).

## 10. RECOMMENDED PRIORITIES

1. Commit/verify Qwen NF4 dirty diff trên GPU.
2. Quyết định scope: local only vs. cloud. Nếu cloud → implement RBAC + PocketBase + rate limiting.
3. Giảm SAM2 pickle risk (wrap load with `weights_only` check / safetensors conversion / model signature).
4. Tăng CI coverage cho engine tests (Windows GPU runner).
5. Fix ruff lints.

## 11. QUESTIONS FOR EXTERNAL REVIEWER

- Is `PipelineQuantizationConfig` usage in `edit_worker.py` missing `components_to_quantize`? Should we explicitly quantize only `transformer`/`text_encoder` to avoid OOM?
- How can we safely load SAM2 checkpoints without trusting `torch.load` pickle?
- What is the minimal RBAC model for a local Windows desktop app?
