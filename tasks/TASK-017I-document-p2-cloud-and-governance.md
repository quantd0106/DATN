# TASK-017I: Document Service P2 Backlog (Cloud Storage, Security & Governance)

**ID:** `TASK-017I` · **Mức ưu tiên:** `P2` *(Cloud & Governance Backlog)* · **Trạng thái:** `DEFERRED (P2)`
**Tham chiếu chuẩn:** [ADR-002](../docs/architect/microservices-v1/ADR-002-document-service.md) · [19_Decisions_Gaps.md](../docs/architect/athena-design/19_Decisions_Gaps.md) · [22_ENVIRONMENTS_DEPLOYMENT.md](../docs/architect/athena-design/22_ENVIRONMENTS_DEPLOYMENT.md)

---

## 1. Mục tiêu

Tập trung toàn bộ các hạng mục **P2** được tách ra từ thiết kế Document Service để không làm phình phạm vi demo `Docker Compose`, tạo lộ trình kỹ thuật rõ ràng khi mở pha Production/Cloud.

---

## 2. Danh sách hạng mục P2 & Điều kiện mở khóa

| Mã | Hạng mục P2 | Thay đổi kỹ thuật dự kiến | Phụ thuộc / Điểm nghẽn (Blocker) |
|---|---|---|---|
| **P2-1** | **GCS & Signed URL Upload** | Cài đặt `GCSStorageBackend` triển khai interface `StorageBackend` (`services/document/app/common/storage.py`); bổ sung flow xin signed PUT URL + finalize xác nhận `SHA-256` để upload tệp `10–20 MB` không bị trần body (~`4.5 MB`) của Vercel Functions | Chốt bucket GCS, IAM Service Account, CORS policy cho trình duyệt |
| **P2-2** | **Quét mã độc (Malware Scan)** | Tích hợp bước scan (ví dụ ClamAV sidecar hoặc Cloud scan hook) trong worker giữa `PENDING` và bước trích xuất IDP; đánh dấu `FAILED` + `error_code` khi phát hiện mã độc | Chọn cơ chế scan phù hợp tài nguyên container/cloud & SLA xử lý |
| **P2-3** | **Hạn mức dung lượng (Storage Quota)** | Giới hạn dung lượng/số tài liệu theo giảng viên hoặc trường (`school_id`/`owner_id`), hiển thị trong `GET /documents/policy` và kiểm tra nguyên tử khi tạo phiên bản | Chưa có Business Rule chốt mức trần quota cho từng vai trò/trường |
| **P2-4** | **Chính sách lưu giữ & Dọn dẹp (`OD-02`, `OD-03`)** | Worker dọn tệp blob không còn tham chiếu (`deleted_at` quá hạn, phiên bản thất bại quá hạn, retention log audit) | **Blocked by `OD-02` & `OD-03`** trong [19_Decisions_Gaps.md](../docs/architect/athena-design/19_Decisions_Gaps.md) — AI Coding Agent không tự đặt thời hạn lưu giữ pháp lý ([AGENTS.md](../AGENTS.md)) |
| **P2-5** | **Gửi thông báo khi chia sẻ tài liệu** | Tiêu thụ outbox `document.grant.changed` (đã ghi ở P0/P1) và gọi internal notification intake sang Core tạo thông báo in-app cho học sinh | Bổ sung consumer tại `services/document` gọi internal endpoint notification tương ứng trên `services/core` |
| **P2-6** | **Deploy lên Cloud Run (Production Cloud)** | Đóng gói Document API + Worker lên Cloud Run / Cloud Run Jobs + Cloud SQL PostgreSQL + VPC connector tới Core | Phụ thuộc P2-1 (GCS) và kiến trúc Secret Manager / Cloud Armor theo [22_ENVIRONMENTS_DEPLOYMENT.md](../docs/architect/athena-design/22_ENVIRONMENTS_DEPLOYMENT.md) |

---

## 3. Ranh giới triển khai cho pha P2

- **Tách riêng từng hạng mục `P2-1..P2-6`** sau khi ghi nhận rõ quyết định cho blocker tương ứng trong `19_Decisions_Gaps.md` và `ADR-002`.
- Mọi thay đổi schema ở P2 bắt buộc tạo **migration mới** (`0002_...`), tuyệt đối không sửa `0001_initialize_document_schema.py`.
- `LocalVolumeStorage` vẫn phải giữ làm mặc định cho chế độ `Docker Compose` local dev/demo.
