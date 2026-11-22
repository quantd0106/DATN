# OPEN DECISIONS, ASSUMPTIONS, RISKS & CHANGE CONTROL

**Mã:** LUMI-GOV-001 · **Phiên bản:** 1.0-DRAFT · **Đối tượng:** Product/Operator/Architecture/QA.

## 1. Quy tắc quản lý quyết định

- **DECIDED:** Có căn cứ trực tiếp từ Business Rules/ADR hoặc policy review được ghi nhận.
- **PROPOSED:** Viết thiết kế đề xuất để thảo luận, không được triển khai như quyết định.
- **PENDING:** Thiếu thông tin đủ để chốt; không tự đặt default nghiệp vụ.
- **DEFERRED:** Đã hoãn theo phạm vi hoặc chưa thể kiểm thử.
- **CONFLICT:** Tài liệu/mã khác nhau; phải trỏ nguồn chuẩn có thẩm quyền để đối chiếu và cập nhật đồng bộ.

## 2. Bảng quyết định còn mở

| ID | Chủ đề | Quyết định phải có | Rủi ro nếu bỏ qua | Trạng thái | Tác động |
|---|---|---|---|---|---|
| OD-01 | Identity verification Student | Bằng chứng xác minh, đại diện hợp lệ và quy trình bàn giao mã thực | Chiếm tài khoản hoặc xử lý dữ liệu sai quyền | PENDING OPERATOR | Production onboarding |
| OD-02 | Data retention | Theo loại tài khoản, roster, posts, docs, notes, submissions, grades, evidence, logs, backups | Xóa sai/không thể đáp ứng yêu cầu | PENDING OPERATOR | All services |
| OD-03 | DELETE executor | Service sở hữu, bước xóa/ẩn danh, audit/result/rollback | COMPLETED giả hoặc mất dữ liệu cần giữ | PENDING OPERATOR | Data requests |
| OD-04 | Backups/DR | RPO/RTO/restore drill và trách nhiệm | Không có khả năng phục hồi được chứng minh | PENDING OPS | Production |
| OD-05 | Multi-worker Core | Có cần >1 worker/replica; shared limiter/idempotency store | Retry không bền và giới hạn không đồng bộ | PENDING OPERATING TARGET | Scale |
| OD-06 | Document storage | **P0/P1 IMPLEMENTED — VERIFIED (`StorageBackend` / `LocalVolumeStorage`):** local Docker volume + ghi nguyên tử theo khóa `source/{document_id}/{version_id}` & `canonical/{document_id}/{version_id}.json` + kiểm chứng `source_sha256`, hạn mức 20 MB / 300 trang (`GET /documents/policy`), chống ZIP-bomb/magic-byte; **P2 DEFERRED (`TASK-017I`):** GCS signed URL upload, malware scan, quota, encryption key mgmt | Rò rỉ tài liệu/cost/timeouts khi lên cloud | P0/P1 IMPLEMENTED / P2 DEFERRED | G3 |
| OD-07 | Document grants | **P0/P1 IMPLEMENTED — VERIFIED (`ADR-002`):** Core action `manage_class_documents`, xác thực `POST /internal/v1/auth/introspect` fail closed, `read_document_source` + active grant + che `source_quote` H6/L6 | Revoked data vẫn truy cập được | P0/P1 IMPLEMENTED | G3 |
| OD-08 | Document processing | **P0/P1 IMPLEMENTED — VERIFIED (`ADR-002`):** 5 trạng thái DB (`PENDING/PROCESSING/SUCCESS/NEEDS_REVIEW/FAILED`), `SUPERSEDED` dẫn xuất theo H3, chủ sở hữu `/accept` cho `NEEDS_REVIEW`, lease/`SKIP LOCKED` trên `document_versions`, IDP xuất Canonical tách khỏi lập chỉ mục | Lost version, broken notes, tắc luồng OCR/DOCX | P0/P1 IMPLEMENTED | G3 |
| OD-09 | Learning schema | Practice/Assessment/Assignment tách bảng hay shared model | Logic chấm/duyệt đan xen | PROPOSED ADR-003 | G4 |
| OD-10 | Transfer pending attempt | Cutoff, quyền tiếp tục bài, giáo viên chấm historical work | Mất bài hoặc cấp quyền trái rule | PENDING PRODUCT | G4/Core contract |
| OD-11 | Grade policy | Điểm scale, weighting, late penalty, best/latest/average, sửa điểm công bằng | Đánh giá sai, khó khiếu nại | PENDING PRODUCT | G4 |
| OD-12 | Appeal | Hạn/phạm vi xét lại điểm, ai phê duyệt, evidence | Từ chối quyền lịch sử hoặc để vô hạn | PENDING PRODUCT | G4 |
| OD-13 | Learning timer | Server deadline, lazy/server job, offline semantics | Mất bài hoặc nộp muộn không minh bạch | PROPOSED DESIGN | G4 |
| OD-14 | Event contracts | Document outbox `document.version.published`, `document.grant.changed`, `document.deleted` đã cài đặt trong `services/document/` (`TASK-017B/C`); AI consumer hoàn thiện ở `TASK-017F` / `TASK-018`; Learning/LI G4–G5 còn đề xuất | Không nhất quán Calendar/Analytics/Index | Document IMPLEMENTED (consumer PENDING) / G4–G5 PROPOSED | G3–G5 |
| OD-15 | Learner evidence | Topic taxonomy, provenance và independent coverage | Mastery không có cơ sở | PENDING PRODUCT/QA | G5 |
| OD-16 | Mastery algorithm | Threshold, weights, calibration dataset và fairness | Kết luận năng lực sai | PENDING DOMAIN EVALUATION | G5 |
| OD-17 | Report privacy | Các chiều lọc/suppression/differencing/export | Tái định danh học sinh | PROPOSED ADR-005 | G5 |
| OD-18 | Web release acceptance | Danh sách Core 0.1 screen/E2E exit gate | Chỉ build được nhưng chưa hoạt động | PENDING HUMAN ACCEPTANCE | Core MVP |
| OD-19 | Production runtime provenance | Redeploy cùng SHA/image digest/PUBLIC_WEB_URL | Email link sai và source drift | PENDING OPS EVIDENCE | Cloud staging/mock |
| OD-20 | AI Service & RAG | Thiết kế, triển khai Tutor/Quiz & kiểm thử `QAS-06` (>= 100 câu GGUF thật) | Lệch pipeline vector/LLM hoặc thiếu kiểm chứng model | **IMPLEMENTED (gate QAS-06 mở)** | `microservices-v1/ai/` |

## 3. Những điểm **không** được coi là open decision nữa

- Một trường chính khóa THCS/THPT khối 6–12, 3 global technical roles (Business Rules).
- Chỉ Admin tạo lớp và cấp tài khoản Student; không join code/self-join/self-leave (A1–A8/F1).
- PRIMARY/ASSISTING là teaching assignments, PRIMARY phê duyệt thao tác nhạy cảm thuộc Learning (M2/D4/I6).
- Core API và Web có code live, không còn ở trạng thái skeleton theo `SYSTEM_DESIGN.md` 31/10.
- Mã activation Student mặc định 24h, one-time và do Admin bàn giao theo contract đã triển khai.
- `users.school_id` nullable FK migration 0014 đã được ghi trong SDD. Điều này không phải chứng cứ tất cả tài khoản legacy thật đã được map.

## 4. Các xung đột có thể gây hiểu nhầm

| ID | Nguồn lịch sử và nguồn mới | Cách xử lý tài liệu |
|---|---|---|
| CF-01 | Một số `BUSINESS_RULES.md` mục “PENDING DESIGN/lưu đồ chưa triển khai” viết ở thời điểm ban đầu; Core/Document/AI/Learning Design/API/migration đã triển khai sau | Giữ Business Rules làm chuẩn nghiệp vụ, dùng API/ORM/migration cho hiện trạng code; không tự ý chỉnh sửa `BUSINESS_RULES.md` |
| CF-02 | Tài liệu Frontend/UI/UX cần phản ánh đúng 71 routes và 3 BFF proxy hiện hành | Đã đồng bộ vào [apps/web/README.md](../../../apps/web/README.md), [UI_UX_DESIGN.md](../../../apps/web/docs/UI_UX_DESIGN.md) và [13_UIUX.md](13_UIUX.md) |
| CF-03 | Các bằng chứng kiểm thử lịch sử cuối 10/2026 chỉ gắn với commit thời điểm đó | Luôn ghi rõ commit/thời điểm cho từng lần chạy; không suy diễn kết quả cũ cho commit mới nếu chưa chạy lại CI/test |
| CF-04 | Business Rules Product MVP bao gồm AI, nhưng bộ hồ sơ này loại AI theo yêu cầu hiện tại | Đánh dấu AI `OUT OF SCOPE`, không sửa Business Rules gốc |
| CF-05 | QAS-01/02/06/07/08 có yếu tố AI; công việc đang hoãn | Không tạo test AI hoặc báo QAS-06 PASS; chỉ giữ nguyên nguồn tham khảo QAS |

## 5. Assumption log

| ID | Giả định thực dụng | Cách kiểm chứng |
|---|---|---|
| AS-01 | Giữ tài liệu thiết kế dạng Markdown trong monorepo để dễ cập nhật đồng bộ với mã nguồn | Rà soát định kỳ định dạng và vị trí thư mục |
| AS-02 | Dữ liệu mẫu E2E chỉ là synthetic | Kiểm tra seed và test deployment |
| AS-03 | Future services phải có DB/schema/storage riêng theo ownership, nhưng không nhất thiết máy chủ riêng | ADR/architecture review trước triển khai |
| AS-04 | Mọi route trên Web không có backend thì hiển thị unavailable live | Đối chiếu integration registry, browser QA |
| AS-05 | Các use cases chưa được stakeholder phỏng vấn độc lập; chúng diễn giải rules đã có | Validate với Product trước signoff SRS/FRS |

## 6. Quy trình Change Request (CR)

1. Người đề xuất tạo CR gồm mô tả, Rule IDs liên quan, motivation/impact/priority và release.
2. Product/Operator quyết định thay đổi nghiệp vụ và chính sách; Architecture review dữ liệu/service boundaries.
3. Cập nhật Business Rules/ADR (nếu cần) rồi BRD/SRS/FRS/UC/LLD/API/ERD/DFD/RTM.
4. Cập nhật code/migration/tests theo approved contract; chạy regression và security tests.
5. Kiểm thử liên kết evidence/source revision; xác nhận nghiệm thu theo từng gate.
6. Ghi revision/date và migration compatibility; không ghi đè lịch sử.

## 7. Template ADR

```markdown
# ADR-XXX — <Quyết định>
Status: PROPOSED | ACCEPTED | SUPERSEDED
Date: YYYY-MM-DD
Context: <vấn đề, Rule ID, NFR>
Decision Drivers: <độ tin cậy, bảo mật, vận hành, chi phí>
Alternatives: <2-3 phương án và tradeoffs>
Decision: <chỉ điền khi duyệt>
Consequences: <rủi ro, migration, testing, rollback>
Verified commit/Approval: <commit và ngày>
Links: <SRS/LLD/PR/test evidence>
```

## 8. Checklist review bộ tài liệu

- [ ] Business Rules và scope không bị thay đổi ngầm.
- [ ] AI không xuất hiện trong FRS/LLD/API/test; mọi tham chiếu chỉ là outside-scope placeholder/rule bảo lưu.
- [ ] Core model names/fields/API được đối chiếu với source hiện hành và mục nào chưa xác nhận có ghi note.
- [ ] Future schema/API được gắn **PROPOSED**, không giả đã triển khai.
- [ ] Mỗi requirement được link tới use case/test/diagram; coverage gap được ghi rõ trạng thái.
- [ ] Security/privacy, admin actions, data retention và teen data được xử lý có policy.
- [ ] Chiến lược migration, deployment, retry, monitoring, backup được mô tả với mức chắc chắn phù hợp.
- [ ] Human approval và technical verification không được gộp chung.