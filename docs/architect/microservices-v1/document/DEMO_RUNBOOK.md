# Kịch bản bảo vệ Document Service

Luồng chính bám Mục 5.7 của `Bao_cao_DATN.docx`. Dành nhiều thời gian nhất cho
bước 4 (xử lý và chia sẻ) và bước 7 (thu hồi). Dùng ba hồ sơ trình duyệt riêng
cho Admin, giáo viên chính và học sinh để chuyển vai trò ngay.

## Chuẩn bị Compose và tài khoản

Chạy PowerShell ở gốc repo. Dùng Docker Compose v2 và môi trường Python 3.12 của
[Document Service](../../../../services/document/README.md). Project mới có
volume riêng. Đoạn lệnh dừng nếu repo đã có `.env`.

```powershell
if (Test-Path -LiteralPath .env) { throw 'Gốc repo đã có .env. Dùng checkout demo riêng.' }
$python = '.\services\document\.venv\Scripts\python.exe'
$script = '.\services\document\scripts\verify_compose_e2e.py'
$project = 'lumi-doc-demo-' + (Get-Date -Format 'yyyyMMddHHmmss')
$fixtures = Join-Path ([IO.Path]::GetTempPath()) $project
$school = [guid]::NewGuid().ToString()
$web = 'http://127.0.0.1:13000'
$origin = 'http://localhost:13000'
$env:CORE_SEED_ADMIN_PASSWORD = & $python -c "import secrets; print(secrets.token_hex(16))"
$env:E2E_LECTURER_PASSWORD = & $python -c "import secrets; print(secrets.token_hex(16))"
$env:E2E_ASSISTING_PASSWORD = & $python -c "import secrets; print(secrets.token_hex(16))"
$env:INTERNAL_SERVICE_SECRET = & $python -c "import secrets; print(secrets.token_hex(32))"
$env:DOCUMENT_DB_PASSWORD = & $python -c "import secrets; print(secrets.token_hex(16))"
$coreKey = & $python -c "import secrets; print(secrets.token_hex(32))"
$pgPassword = & $python -c "import secrets; print(secrets.token_hex(16))"

@"
POSTGRES_DB=dev_rag_course
POSTGRES_USER=rag_course
POSTGRES_PASSWORD=$pgPassword
POSTGRES_PORT=15432
DATABASE_URL=postgresql+psycopg://rag_course:${pgPassword}@db:5432/dev_rag_course
DOCUMENT_DB_USER=document_svc
DOCUMENT_DB_PASSWORD=$env:DOCUMENT_DB_PASSWORD
DOCUMENT_DATABASE_URL=postgresql+psycopg://document_svc:${env:DOCUMENT_DB_PASSWORD}@db:5432/dev_rag_course
SECRET_KEY=$coreKey
INTERNAL_SERVICE_SECRET=$env:INTERNAL_SERVICE_SECRET
CORE_PORT=8000
DOCUMENT_PORT=8001
WEB_PORT=13000
WEB_ORIGIN=$origin
PUBLIC_WEB_URL=$origin
CORS_ORIGINS=["$origin"]
COOKIE_SECURE=false
CORE_API_URL=http://core:8000
DOCUMENT_API_URL=http://document:8001
NEXT_PUBLIC_DATA_MODE=live
DOCUMENT_WORKER_LEASE_SECONDS=10
"@ | Set-Content -LiteralPath .env -Encoding ascii

docker compose -p $project up --build -d --wait --wait-timeout 240
if ($LASTEXITCODE -ne 0) { throw 'Compose chưa sẵn sàng.' }
docker compose -p $project exec -T -e CORE_SEED_ADMIN_PASSWORD core python -m app.maintenance seed-dev `
  --environment development --expected-database dev_rag_course --school-id $school `
  --school-name 'Lumi Demo School'
if ($LASTEXITCODE -ne 0) { throw 'Seed Core thất bại.' }

& $python -B $script --prepare-demo-logins --web-url $web --origin $origin `
  --compose-project $project --db-name dev_rag_course
if ($LASTEXITCODE -ne 0) { throw 'Chuẩn bị tài khoản giáo viên thất bại.' }
& $python -B $script --write-demo-fixtures $fixtures
if ($LASTEXITCODE -ne 0) { throw 'Tạo tệp demo thất bại.' }
```

| Vai trò | Tài khoản | Cách đăng nhập |
| --- | --- | --- |
| Admin | `admin@seed.example.com` | Mật khẩu trong `CORE_SEED_ADMIN_PASSWORD` |
| Giáo viên chính | `primary@seed.example.com` | Mật khẩu trong `E2E_LECTURER_PASSWORD` |
| Giáo viên phối hợp | `assisting@seed.example.com` | Mật khẩu trong `E2E_ASSISTING_PASSWORD` |
| Học sinh demo | Chọn username mới tại bước 1 | Dùng mã một lần do Admin cấp rồi đặt mật khẩu |

Seed tạo `SEED-MATH`, lớp hành chính `Seed 9A`, lớp môn học `Seed Math 9 AUTO`
và `Seed Math 9 MANUAL`. Tài khoản `seed.student.1` và `seed.student.2` đã kích
hoạt với mật khẩu ngẫu nhiên do seed bỏ đi. `seed.student.3` còn chờ kích hoạt.
Mở trình duyệt bằng `http://localhost:13000`. Script kết nối qua IP và gửi
`Origin: http://localhost:13000`, khớp cả `WEB_ORIGIN` và CORS của Core.

## Kiểm chứng trước buổi bảo vệ

```powershell
& $python -B $script --web-url $web --origin $origin --compose-project $project --db-name dev_rag_course
```

Lưu kết quả từng nhóm, thời gian và số liệu min/p50/p95 của ba request Web.
Các số đo là kết quả tuần tự trên Compose local. Bản chạy đầy đủ gồm lần giết
worker, quan sát `attempt_count` và lần dừng Document API. Nếu chỉ chạy HTTP,
thêm `--skip-docker-faults` và dùng hai mật khẩu giáo viên đã biết.

Tệp mẫu nằm trong `$fixtures`. Dùng `lesson.pdf` (hơn 10 MiB), `lesson.pptx`,
`lesson.docx`, `lesson.txt` và `lesson.md` cho luồng thành công. `review.docx`
có công thức OMML thiếu ngoặc, tạo cờ `LATEX_INVALID`. `blank.pdf` qua kiểm tra
upload rồi lỗi ở IDP. `revision.pdf` có nội dung mới để phân biệt bản đang phục vụ.

## Luồng chính 5 phút

| Bước | Thời gian | Thao tác và kết quả cần chỉ |
| --- | --- | --- |
| 1 | 0:00–0:30 | Admin vào `/admin/accounts/students/new`, cấp username và mã một lần. Học sinh mở `/activate`, đặt mật khẩu và đăng nhập. |
| 2 | 0:30–1:05 | Thêm học sinh vào roster `Seed 9A`. Tại `/admin/classes/new`, tạo lớp khối 9 cho `SEED-MATH`, chọn `AUTO`, `Seed 9A`, giáo viên chính và phối hợp. Mở màn ghi danh để chỉ học sinh có trong lớp. |
| 3 | 1:05–1:30 | Giáo viên mở bảng tin lớp mới và đăng bài ngắn. Học sinh mở bài, bình luận và kiểm tra thông báo của lớp. |
| **4** | **1:30–3:10** | Giáo viên mở `/teacher/library`, chỉ hạn mức 20 MiB và 300 trang/slide. Tải `lesson.pdf`, mở `/teacher/library/{documentId}`, quan sát trạng thái xử lý rồi sẵn sàng (`SUCCESS` ở API). Tải `review.docx`, mở phiên bản **Cần rà soát** rồi bấm **Chấp nhận**. Chia sẻ vào lớp đang hoạt động. Học sinh vào `/student/classes/{classId}/documents/{documentId}`, xem nội dung Canonical, lưu ghi chú, đánh dấu trang và tải tệp gốc. |
| 5 | 3:10–3:40 | Khi có môi trường AI, học sinh hỏi nhanh rồi hỏi theo từng bước, mở trích dẫn đúng trang và thử câu ngoài tài liệu. Với Compose trong runbook, mở lại trang tương ứng trong Reader và dùng video mẫu cho phần hỏi đáp. |
| 6 | 3:40–4:10 | Khi có môi trường sinh câu hỏi, giáo viên mở ma trận đề, sinh, duyệt và xuất câu hỏi. Học sinh làm luyện tập và xem giải thích. Dùng video mẫu của Mục 5.7 nếu chức năng này chưa bật. |
| **7** | **4:10–5:00** | Giáo viên chọn **Thu hồi chia sẻ** cho lớp demo. Học sinh tải lại Reader đã mở và kiểm tra nguồn/tải tệp bị chặn, trong khi chữ tự viết vẫn xem và sửa được tại `/student/notes`. Dùng kết quả nhóm 08 để chỉ `source_quote = null` vì trình soạn ghi chú Web hiện gửi chữ tự viết. Nếu có AI, hỏi lại và kiểm tra nguồn đã thu hồi không còn được truy hồi. Mở hộp xác nhận xóa của **một tài liệu khác** đã chia sẻ ở hai lớp để chỉ cảnh báo B6. |

Chuẩn bị sẵn một PDF đã xử lý và chia sẻ hai lớp, cùng một DOCX đang **Cần rà
soát** để chuyển sang bản chuẩn bị nếu worker đang bận. Giữ video đầy đủ Mục 5.7
để dùng khi máy chủ mô hình hoặc mạng chậm.

## Hỏi sâu 3 phút

1. **0:00–0:55 · H3 và bản lưu.** Giữ `lesson.pdf` v1 đang phục vụ. Chia sẻ lại nếu
   vừa thu hồi ở bước 7, hoặc dùng tài liệu chuẩn bị riêng. Tải
   `blank.pdf` làm v2 và chỉ trạng thái lỗi. Tải lại Reader để thấy học sinh
   vẫn ở v1. Bấm **Thử lại** cho v2. Tải `revision.pdf` làm v3, đợi sẵn sàng
   rồi chỉ v1 mang nhãn **Bản lưu** và v3 đang phục vụ.
2. **0:55–1:35 · Duyệt phiên bản.** Mở bản `review.docx` đang **Cần rà soát**.
   Chỉ kết quả nhóm 04 về quyền giáo viên phối hợp, rồi dùng tài khoản chủ sở
   hữu bấm **Chấp nhận** để phiên bản thành `SUCCESS` với `OWNER_ACCEPTED`.
   Chia sẻ và mở Reader từ phiên học sinh.
3. **1:35–2:15 · Xóa đa lớp.** Mở hộp cảnh báo trên tài liệu đã chia sẻ hai lớp.
   Kết quả nhóm 09 cho thấy API trả `409` kèm hai ID lớp khi thiếu xác nhận.
   Xác nhận trong giao diện và tải lại Reader học sinh để thấy nguồn bị chặn.
4. **2:15–3:00 · Cô lập dịch vụ.** Mở sẵn hai tab Network. Sau lệnh dừng, tải
   lại danh sách lớp và thư viện. Chỉ `GET /api/v1/classes` trả `200` và
   `GET /api/v1/documents/policy` trả `503` trên phiên đăng nhập. Khôi phục
   dịch vụ và tải lại thư viện sau khi readiness trả `200`.

```powershell
docker compose -p $project stop document
# Thực hiện hai request qua Web trong phiên đã đăng nhập.
docker compose -p $project start document
if ($LASTEXITCODE -ne 0) { throw 'Document chưa khởi động lại.' }
$ready = $false
$deadline = (Get-Date).AddSeconds(60)
do {
    try {
        $ready = (Invoke-RestMethod 'http://127.0.0.1:8001/health/ready' -TimeoutSec 3).status -eq 'ready'
    } catch {
        Start-Sleep -Seconds 1
    }
} until ($ready -or (Get-Date) -ge $deadline)
if (-not $ready) { throw 'Document chưa sẵn sàng sau 60 giây.' }
```

## Dọn dẹp

Chạy trong cùng phiên PowerShell đã tạo project. `down -v` xóa dữ liệu và
tệp gốc trong volume của project demo này.

```powershell
docker compose -p $project down -v
if ($LASTEXITCODE -ne 0) { throw 'Compose chưa dọn xong project demo.' }
Remove-Item -LiteralPath .env
Get-ChildItem -LiteralPath $fixtures -File | Remove-Item
Remove-Item -LiteralPath $fixtures
Remove-Item Env:CORE_SEED_ADMIN_PASSWORD, Env:E2E_LECTURER_PASSWORD, Env:E2E_ASSISTING_PASSWORD
Remove-Item Env:INTERNAL_SERVICE_SECRET, Env:DOCUMENT_DB_PASSWORD
Remove-Variable coreKey, pgPassword
```
