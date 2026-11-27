# TASK-017H: Document Full Compose E2E Suite & Defense Demo Runbook

**ID:** `TASK-017H` · **Mức ưu tiên:** `P0 / P1` *(Wave 1B)* · **Trạng thái:** `DONE — VERIFIED` *(script Compose full E2E & DEMO_RUNBOOK.md hoàn tất; còn bước bấm tay trên trình duyệt theo Runbook)*
**Tham chiếu chuẩn:** [17_Test_Cases.md](../docs/architect/athena-design/17_Test_Cases.md) · [API_CONTRACT.md](../docs/architect/microservices-v1/document/API_CONTRACT.md) · [BUSINESS_RULES.md](../docs/architect/BUSINESS_RULES.md)

---

## 1. Bối cảnh & Mục tiêu

Suite pytest của `services/document/` (`99 passed in 11.59s`) đã chứng minh toàn bộ logic trên PostgreSQL 16 thật, nhưng ở tầng **Docker Compose live full-stack** mới chạy smoke HTTP cho PDF `10,500,004` byte và chặn `>20 MB` (`TASK-017E`), chưa có:
1. Một **script E2E tự động lặp lại được** chạy trực tiếp qua Web Proxy (`http://127.0.0.1:3000/api/v1/*`) trên Compose đang chạy để phủ trọn vẹn các kịch bản **P0 + P1 (`DOC-T01` → `DOC-T13`, `QAS-01/02`, `QAS-10`)**.
2. Một **Runbook thao tác trình duyệt từng bước (`DEMO_RUNBOOK.md`)** phục vụ kiểm thử thủ công E2E và hoàn thành các ô nghiệm thu UI còn mở ở `TASK-017C` / `TASK-017D`.

---

## 2. Phạm vi & File Allowlist

### Được phép tạo / sửa
- `services/document/scripts/verify_compose_e2e.py` *(chạy qua Python 3.12 / stdlib hoặc môi trường dev sẵn có, không cài thêm package runtime vào image production)*
- `docs/architect/microservices-v1/document/DEMO_RUNBOOK.md`
- `services/document/README.md` *(chỉ thêm mục hướng dẫn chạy `verify_compose_e2e.py`)*

### Không được sửa
- `docs/architect/BUSINESS_RULES.md`, `services/document/app/*`, `services/core/*`, `apps/web/*`, `compose.yaml` *(nếu script phát hiện lỗi thật ở backend hoặc Web, xử lý sửa lỗi tại task tương ứng thay vì trộn vào script kiểm thử)*.

---

## 3. Danh sách kịch bản bắt buộc trong `verify_compose_e2e.py` (chạy trên Compose live)

Tất cả request công khai đi qua Web Proxy `http://127.0.0.1:3000/api/v1/*`, giữ cookie session thật từ Core login:

| Nhóm | Kịch bản kiểm chứng live trên Compose | Đối chiếu Rule / Test |
|---|---|---|
| **P0 — Upload 5 định dạng & Validation** | Upload thành công đủ 5 định dạng hợp lệ: `.pdf`, `.pptx`, `.docx`, `.txt`, `.md`; đều lên `SUCCESS` qua `document-worker`. Test file vượt `20 MB` trả `413`; file giả mạo magic-byte / quá số trang / ZIP-bomb trả `422` (`B3`, `B5`) | `DOC-T03`, `DOC-T04`, `B3`, `B5` |
| **P0 — `NEEDS_REVIEW` → Teacher `accept`** | Đưa tệp thực kích hoạt `NEEDS_REVIEW` qua `services/rag/idp/ingest.py` (ví dụ DOCX chứa công thức hoặc PDF scan OCR nhanh nếu deterministic); kiểm tra học sinh **chưa** thấy bản `NEEDS_REVIEW`; giảng viên gọi `accept` bằng `version_id` -> chuyển `SUCCESS`, học sinh đọc được bản mới | `B4`, `H3`, `TASK-017C` |
| **P0 — `H3` fallback & `retry`** | Tạo bản v1 `SUCCESS`, upload bản v2 lỗi (`FAILED` hoặc trước khi `accept`); xác nhận học sinh vẫn đọc bản v1 (`active_version`), danh sách phiên bản đánh dấu bản cũ hợp lệ khi bị thay thế (`SUPERSEDED` dẫn xuất chỉ khi có bản `SUCCESS` mới hơn). Test gọi `retry` với bản `FAILED` | `DOC-T05`, `H3` |
| **P0 — Phân quyền lớp & Thu hồi** | Thử share từ giảng viên không được phân công lớp hoặc lớp `ARCHIVED` -> Core từ chối (`403` / `409`); giảng viên hợp lệ grant lớp `ACTIVE` -> học sinh đọc pages canonical + stream file gốc `200` -> revoke grant -> học sinh đọc & stream tiếp theo bị chặn `403` ngay lập tức (không cache quyền) | `DOC-T06` → `DOC-T10`, `B2`, `B6`, `O1` |
| **P0 — Internal endpoint cho AI** | Kiểm tra `GET /internal/v1/classes/{cid}/documents/active-grants` và `GET /internal/v1/documents/{document_id}/versions/{version_id}/canonical` với header `X-Service-Key` hợp lệ (`INTERNAL_SERVICE_SECRET` từ host/internal test hook phù hợp), và kiểm tra từ chối khi thiếu/sai key (`401/403`) | `ADR-002`, `TASK-018` |
| **P1 — Notes / Bookmarks (`H6`, `L6`, `O2`)** | Học sinh tạo note (có `source_quote`) + bookmark; kiểm tra user khác không đọc/sửa/xóa được (`G5`); revoke grant lớp -> kiểm tra đồng thời trên `/users/me/notes`, `/notes/{note_id}` và `/classes/{cid}/documents/{did}/reader` rằng chữ tự viết vẫn đọc được nhưng `is_source_accessible = false`, `source_quote = null`, `outline = []`, `pages = []`, `revoked_reason = "GRANT_REVOKED"` (`H6/L6`); trên lớp `ARCHIVED` -> vẫn sửa được note/bookmark (`O2`) | `DOC-T11` → `DOC-T13`, `G5`, `H6`, `L6`, `O2` |
| **P1 — Cảnh báo xóa đa lớp (`B6`)** | Cảnh báo `B6` kích hoạt `409` khi `active_grant_count >= 1`; script test với tài liệu đang grant **>= 2 lớp** -> gọi `DELETE /documents/{id}` lần đầu trả `409` kèm danh sách `affected_class_ids`; xác nhận xóa có `confirm_multi_class=true` -> xóa mềm thành công và chặn đọc nguồn | `DOC-T02`, `B6` |
| **P1 — Crash recovery (`QAS-10`)** | Upload file kích thước lớn / tốn thời gian xử lý -> khi `document-versions` đang `PROCESSING` (hoặc mô phỏng `docker compose kill document-worker`), khởi động lại `document-worker` -> chờ hết lease (hoặc cấu hình lease ngắn khi chạy test) -> bản xử lý được pickup lại, không mất file gốc, không làm hỏng bản `SUCCESS` trước | `QAS-10`, `H3` |
| **P0/P1 — Độ trễ & Cô lập dịch vụ (`QAS-01/02/07`)** | Đo p95 thời gian phản hồi endpoint đọc metadata / danh sách / reader trang canonical trên Compose local; kiểm tra `docker compose stop document` không làm chết `GET /api/v1/classes` của Core (`200`) trong khi `/api/v1/documents/policy` trả `503` | `QAS-01`, `QAS-02`, `QAS-07` |

---

## 4. Yêu cầu cho `DEMO_RUNBOOK.md`

Soạn hướng dẫn ngắn gọn cho **kịch bản demo 7 bước / 5 phút** vòng đời tài liệu, đồng thời có phần mở rộng sâu về Document Service:
1. Lệnh bật stack sạch + seed tài khoản demo (`admin`, `lecturer`, `assisting_lecturer`, `student`) và bộ file mẫu (`10 MB` PDF, DOCX có công thức `NEEDS_REVIEW`, PPTX, TXT/MD).
2. **Luồng chính 5 phút (7 bước)** (nhấn mạnh Document ở bước 4 và bước 7):
   - Bước 1–3 (`Core`): Admin cấp/kích hoạt học sinh bằng mã một lần → tạo lớp, phân công GV, ghi danh `AUTO` → đăng bảng tin/thông báo.
   - Bước 4 (`Document`): Hiển thị policy `20 MB` / `300 trang` → upload tệp mẫu `10 MB` PDF (`PENDING/PROCESSING -> READY`) → upload DOCX có công thức kích hoạt `NEEDS_REVIEW` → GV bấm **"Chấp nhận phiên bản"** (`/accept`) → chia sẻ vào lớp `ACTIVE` (`manage_class_documents`) → Học sinh mở Reader xem trang Canonical, ghi chú/bookmark và tải tệp gốc.
   - Bước 5–6 (`AI Tutor / Quiz` với `compose.full.yaml`): Đặt câu hỏi & sinh câu hỏi luyện tập / ghi chú đối chiếu nguồn.
   - Bước 7 (`Thu hồi & Bảo vệ dữ liệu`): GV thu hồi grant lớp → Học sinh bị chặn mở nguồn nhưng Note vẫn giữ nguyên chữ tự viết và che `source_quote` (`H6/L6`), đồng thời demo cảnh báo `409` khi xóa tài liệu đang dùng ở nhiều lớp (`B6`).
3. **Kịch bản kiểm tra khả năng cô lập lỗi và fallback (3 phút):**
   - Upload bản v2 lỗi (`FAILED` hoặc trước khi `/accept`) để chứng minh Rule `H3` fallback bản v1 `SUCCESS` & nhãn dẫn xuất **"Bản lưu"** (`is_superseded`).
   - Dừng container `document` (`docker compose stop document`) → `/classes` của Core vẫn trả `200`, Document báo tạm ngừng `503`.
4. Lệnh dọn dẹp sạch `.env` / volume sau demo.

---

## 5. Lệnh nghiệm thu (Verification Gate)

1. Khởi chạy `docker compose up --build -d --wait` (với `.env` tạm trên máy local, xóa `.env` sau khi xong).
2. Chạy `py -3.12 services/document/scripts/verify_compose_e2e.py` (không dùng `--no-verify` hay bỏ qua assertion); toàn bộ các bước bảng Mục 3 phải in rõ trạng thái `PASS` và số đo thời gian `QAS-01/02`.
3. Kiểm tra bằng mắt/trình duyệt tối thiểu các màn `/teacher/library`, `/teacher/library/[id]`, `/student/reader` theo `DEMO_RUNBOOK.md`.

---

## 6. Bằng chứng kiểm định (2026-11-22)

- **Compose Live E2E (`lumi-verify-017`, `test_lumi_017`, `DOCUMENT_WORKER_LEASE_SECONDS=10`):**
  - Lệnh: `services/document/.venv/Scripts/python.exe -B services/document/scripts/verify_compose_e2e.py --web-url http://127.0.0.1:13000 --origin http://localhost:13000 --core-url http://127.0.0.1:18000 --document-url http://127.0.0.1:18001 --compose-project lumi-verify-017 --db-name test_lumi_017`
  - Kết quả: **`Total 13 PASS, 0 FAIL, 0 SKIP (72.41s including setup)`**:
    - `01 Policy and auth fail-closed` (`0.09s`), `02 Ingress validation` (`0.55s`: `413` cho `20,971,521` byte & `422` cho empty/fake/spoofed/encrypted/>300 trang), `03 All five formats via real IDP` (`7.32s`: `PDF` `10,504,487` byte, `PPTX`, `DOCX`, `TXT`, `MARKDOWN`), `04 NEEDS_REVIEW and owner-only accept` (`1.52s`: `LATEX_INVALID` → `OWNER_ACCEPTED`), `05 H3 fallback, retry and derived superseded` (`5.78s`), `06 Class grants and immediate revocation` (`2.23s`), `07 Internal AI endpoints` (`1.87s`), `08 Private notes, bookmarks and archived class` (`1.82s`: `is_source_accessible=false`, `source_quote=null`, `O2`), `09 Multi-class delete warning` (`1.50s`: `409` với 2 class IDs), `10 Worker crash and lease recovery` (`27.91s`: `attempt_count 1 -> 2` sau `docker compose kill document-worker`), `11a Web latency samples` (`2.85s`), `11b Database role isolation` (`0.66s`), `11c Document service outage and recovery` (`12.50s`).
  - **Số đo độ trễ tuần tự local Compose (`n=20` qua Web Proxy):**
    - `GET /documents`: `min=35.0 ms`, `p50=37.1 ms`, `p95=40.0 ms`
    - `GET /classes/{cid}/documents`: `min=34.5 ms`, `p50=35.5 ms`, `p95=37.7 ms`
    - `GET /classes/{cid}/documents/{did}/reader`: `min=28.7 ms`, `p50=30.2 ms`, `p95=32.4 ms`
  - `--write-demo-fixtures` sinh thành công `8` tệp mẫu trong `0.57s` (gồm `lesson.pdf` `10,504,487` byte).
  - **Ghi nhận kỹ thuật từ [student-reader-view.tsx](../apps/web/src/features/document/views/student-reader-view.tsx#L74):** Trình soạn ghi chú trên giao diện Web hiện chưa gửi trường `source_quote` khi lưu mới; hành vi che `source_quote = null` theo `H6/L6` được chứng minh đầy đủ ở tầng API bởi nhóm `08` trong `verify_compose_e2e.py` và hướng dẫn thuyết trình rõ trong [DEMO_RUNBOOK.md](../docs/architect/microservices-v1/document/DEMO_RUNBOOK.md).
  - *(Thao tác click chuột thủ công trên trình duyệt để kiểm thử end-to-end giao diện sẽ thực hiện theo `DEMO_RUNBOOK.md`.)*
