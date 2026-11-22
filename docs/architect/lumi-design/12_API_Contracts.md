# DANH MỤC HỢP ĐỒNG GIAO TIẾP GIỮA CÁC THÀNH PHẦN

**Mã tài liệu:** LUMI-API-001 · **Trạng thái:** Tài liệu điều hướng nguồn chuẩn.

## 1. Hợp đồng API của dịch vụ lõi

Hợp đồng giao tiếp **đã triển khai và được duy trì trực tiếp** tại [Core API Contract](../microservices-v1/core/API_CONTRACT.md). Mô tả kỹ thuật chi tiết và OpenAPI thực tế phải được đối chiếu cùng mã nguồn và kết quả kiểm thử tại phiên bản được phát hành.

Không sao chép danh sách endpoint sang bộ hồ sơ tổng thể để tránh hai đặc tả có thể khác nhau khi dự án cập nhật.

## 2. Nguyên tắc giao tiếp và phân quyền

- Yêu cầu từ trình duyệt phải được xác thực, kiểm tra quyền và phạm vi trường/lớp ở phía máy chủ.
- Dịch vụ sở hữu dữ liệu chịu trách nhiệm quyết định quyền truy cập tại thời điểm sử dụng.
- Không truy cập trực tiếp cơ sở dữ liệu của dịch vụ khác.
- Mọi giao diện hoặc thông điệp mới phải mô tả rõ phiên bản, cấu trúc dữ liệu, lỗi, xác thực, kiểm toán và kiểm thử hợp đồng.

## 3. Document, AI và Learning Services (Đã triển khai) và Learner Intelligence (PROPOSED)

- **Quản lý tài liệu (Document Service — `P0/P1 IMPLEMENTED — VERIFIED`):** Thiết kế, hợp đồng API (**20 public + 2 internal endpoints**), 12 hàm Web port và Proxy allowlist đã triển khai và kiểm chứng trên PostgreSQL 16 (`99 passed in 11.59s`) + Compose smoke (`13/13 PASS` tại `verify_compose_e2e.py`) theo [ADR-002](../microservices-v1/ADR-002-document-service.md), [Document DESIGN](../microservices-v1/document/DESIGN.md) và [Document API Contract](../microservices-v1/document/API_CONTRACT.md); kèm phần mở rộng nội bộ Core (`POST /internal/v1/auth/introspect`, `manage_class_documents`) tại [Core API Contract](../microservices-v1/core/API_CONTRACT.md). Hạng mục P2 mở rộng (GCS/Cloud Run/quota/malware) theo dõi ở [TASK-017I](../../../tasks/TASK-017I-document-p2-cloud-and-governance.md).
- **Trợ lý AI & Sinh câu hỏi (AI Service + RAG — `IMPLEMENTED`):** Schema `ai` (`7` bảng, `2` migration), `services/rag` + `services/ai`, cung cấp **15 thao tác Web qua BFF `/api/ai/v1/*`** (7 Tutor: settings, sessions, streaming SSE `/stop`, citations; 8 Quiz: matrix/generate/batch/question regenerate/review/export) và **6 internal endpoints** (`/internal/v1/document-versions/{id}/index`, `/index/failure`, `/grant-invalidation`, `/classes/{id}/tutor/topic-summary`, `/explain-mistake`, `/assignment/grade-hint`). Xem [AI DESIGN](../microservices-v1/ai/DESIGN.md) và [AI API Contract](../microservices-v1/ai/API_CONTRACT.md). Gate mở: `QAS-06` (>= 100 câu với GGUF thật) và tự động hóa consumer `document.version.published`.
- **Học tập, Chấm điểm & Phúc khảo (Learning Service — `IMPLEMENTED`):** Schema `learning` (`18` bảng, `4` migration), lưu tệp bài nộp `/var/lib/learning-blobs` (`25 MiB` mặc định), cung cấp **57 thao tác Web qua BFF `/api/learning/v1/*`** (Question Bank, Practice, Assessment, Assignment, Grading, Appeals, Corrections) và **3 internal endpoints** (`/internal/v1/assessment-lock`, `/quiz-items/import`, `/classes/{id}/grades/confirmed`), đồng thời đẩy deadline/sự kiện về Core qua `PUT /internal/v1/calendar/learning-deadlines` và `POST /internal/v1/notifications/learning-events`. Xem [Learning DESIGN](../microservices-v1/learning/DESIGN.md) và [Learning API Contract](../microservices-v1/learning/API_CONTRACT.md). Gate mở: `QAS-03` (`1,000` ca concurrency) và full `TP-01`/`QAS-05`.
- **Học tập (Learning) và Phân tích học tập (Learner Intelligence):** **chưa có hợp đồng API được phê duyệt**. Các yêu cầu nghiệp vụ tương ứng nằm trong các tài liệu FRS; việc xác định đường dẫn, cấu trúc dữ liệu và giao tiếp sẽ được thực hiện ở giai đoạn thiết kế riêng.

**Không đặc tả API cho dịch vụ trí tuệ nhân tạo trong giai đoạn này.**
