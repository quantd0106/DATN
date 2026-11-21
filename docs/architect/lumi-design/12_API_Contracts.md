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

## 3. Các dịch vụ chưa triển khai

- **Quản lý tài liệu (Document Service):** Đã có thiết kế và hợp đồng API P1 (`DESIGN BASELINE — READY FOR IMPLEMENTATION`, chưa triển khai code) tại [ADR-002](../microservices-v1/ADR-002-document-service.md), [Document DESIGN](../microservices-v1/document/DESIGN.md) và [Document API Contract](../microservices-v1/document/API_CONTRACT.md); kèm phần mở rộng nội bộ Core (`POST /internal/v1/auth/introspect`, `manage_class_documents`) ghi chú tại [Core API Contract](../microservices-v1/core/API_CONTRACT.md).
- **Học tập (Learning) và Phân tích học tập (Learner Intelligence):** **chưa có hợp đồng API được phê duyệt**. Các yêu cầu nghiệp vụ tương ứng nằm trong các tài liệu FRS; việc xác định đường dẫn, cấu trúc dữ liệu và giao tiếp sẽ được thực hiện ở giai đoạn thiết kế riêng.

**Không đặc tả API cho dịch vụ trí tuệ nhân tạo trong giai đoạn này.**
