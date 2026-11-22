# PHẠM VI THIẾT KẾ CHI TIẾT CÁC DỊCH VỤ MỞ RỘNG & ĐỀ XUẤT (`PROPOSED`)

**Mã tài liệu:** LUMI-LLD-FUT-001 · **Trạng thái:** `Document`, `AI` và `Learning` đã triển khai và có tài liệu thiết kế riêng; **chỉ `Learner Intelligence` còn ở trạng thái đề xuất chưa phê duyệt thiết kế chi tiết (`PROPOSED`)**.

- **Document Service (`P0/P1 IMPLEMENTED — VERIFIED`):** Đã phê duyệt và triển khai tại [ADR-002](../microservices-v1/ADR-002-document-service.md), [Document DESIGN](../microservices-v1/document/DESIGN.md) và [Document API Contract](../microservices-v1/document/API_CONTRACT.md) (`P2` theo dõi tại [TASK-017I](../../../tasks/TASK-017I-document-p2-cloud-and-governance.md)).
- **AI Service + RAG (`IMPLEMENTED`):** Đã triển khai và ghi nhận kiến trúc tại [AI DESIGN](../microservices-v1/ai/DESIGN.md) và [AI API Contract](../microservices-v1/ai/API_CONTRACT.md) (gate mở: `QAS-06` với GGUF thật).
- **Learning Service (`IMPLEMENTED`):** Đã triển khai và ghi nhận kiến trúc tại [Learning DESIGN](../microservices-v1/learning/DESIGN.md) và [Learning API Contract](../microservices-v1/learning/API_CONTRACT.md) (gate mở: `QAS-03`, `TP-01`).
- **Phân tích học tập (`Learner Intelligence`):** Chưa có triển khai phía máy chủ (`PROPOSED` tại `TASK-020`). Vì vậy, hồ sơ chuẩn **không ban hành mô hình cơ sở dữ liệu vật lý, API hoặc cấu hình hạ tầng** cho dịch vụ này.

## Nội dung được quyết định ở mức nghiệp vụ

- Quản lý tài liệu (`Document`): xem [Đặc tả chức năng tài liệu](04_FRS_Document.md), các quy tắc B1–B7, H3, H6, L6 và O1–O2.
- Trợ lý AI & Sinh câu hỏi (`AI` + `RAG`): xem [AI DESIGN](../microservices-v1/ai/DESIGN.md), các quy tắc B1, C1–C4, D1–D8, H6.
- Học tập (`Learning`): xem [Đặc tả chức năng học tập](05_FRS_Learning.md), các quy tắc D0–D8, I1–I7, M1–M4 và O3–O5.
- Phân tích học tập (`Learner Intelligence`): xem [Đặc tả chức năng phân tích](06_FRS_Intelligence.md), các quy tắc E1–E6, J1–J5 và O6–O8.
- Ranh giới trách nhiệm được mô tả trong [kiến trúc tổng thể](08_HLD.md) và [sơ đồ các phân hệ](21_ARCHITECTURE_ATLAS.md).

## Điều kiện thiết kế chi tiết

Trước khi chuyển từ đặc tả nghiệp vụ sang thiết kế triển khai, mỗi dịch vụ cần có quyết định về mô hình dữ liệu, quyền truy cập, giao tiếp giữa dịch vụ, xử lý lỗi, cơ chế phục hồi, bảo mật, phương án triển khai và bộ kiểm thử. Mọi nội dung đề xuất phải có người phê duyệt và được kiểm tra đối chiếu với quy tắc nghiệp vụ hiện hành.

**Dịch vụ trí tuệ nhân tạo không thuộc phạm vi thiết kế của bộ hồ sơ này.**
