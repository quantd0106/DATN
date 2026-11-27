# Athena Web

Giao diện web của nền tảng **Athena — RAG Course Assistant**, xây dựng trên **Next.js 15 (App Router)**, **React 19** và **TypeScript**. Ứng dụng cài đặt **71 product routes** (`apps/web/src/config/routes.ts`) cùng trang chuyển hướng phiên gốc `app/page.tsx` (`72` tệp `page.tsx`), chạy với kiến trúc same-origin BFF proxy và adapter hai chế độ tách biệt (`live` mặc định và `mock` preview cục bộ).

Xem tài liệu chi tiết:
- Kiến trúc frontend & biên tích hợp: [docs/FRONTEND.md](docs/FRONTEND.md)
- Đặc tả thiết kế UI/UX & bản đồ 71 routes: [docs/UI_UX_DESIGN.md](docs/UI_UX_DESIGN.md)
- Nhật ký hoàn thiện UI/UX & đồng bộ live: [docs/UI_UX_REFINEMENT.md](docs/UI_UX_REFINEMENT.md) · [docs/UX_LIVE_FIXES.md](docs/UX_LIVE_FIXES.md)
- Hướng dẫn kiểm thử live cục bộ: [docs/LOCAL_LIVE_TESTING.md](docs/LOCAL_LIVE_TESTING.md)
- Bảng trạng thái tích hợp theo năng lực: [src/lib/data/integrations.ts](src/lib/data/integrations.ts)

## Yêu cầu môi trường & chạy trong workspace

Yêu cầu **Node.js `>= 22.17.0`** và `npm`. Chạy từ thư mục `apps/web`:

```powershell
npm ci
if (!(Test-Path .env.local)) { Copy-Item .env.example .env.local }
npm run dev
```

Chế độ `live` là mặc định. Các biến server-only trong `.env.local` (xem `.env.example`):
- `CORE_API_URL`: origin của Core Service (mặc định local `http://localhost:8000`) — phục vụ qua proxy `/api/v1/*`.
- `DOCUMENT_API_URL`: origin của Document Service (mặc định local `http://localhost:8001`) — phục vụ qua proxy `/api/v1/*` (kèm multipart upload & binary stream).
- `AI_API_URL`: origin của AI Service (mặc định local `http://localhost:8002`) — phục vụ qua proxy `/api/ai/v1/*` (kèm SSE stream Tutor).
- `LEARNING_API_URL`: origin của Learning Service (mặc định local `http://localhost:8003`) — phục vụ qua proxy `/api/learning/v1/*`.
- `WEB_ORIGIN`: exact public origin của Web (ví dụ `http://localhost:3000`) dùng để kiểm tra `Origin` chống CSRF tại BFF proxy và phải khớp `CORS_ORIGINS` ở Core.

Không đưa bất kỳ `INTERNAL_SERVICE_SECRET`, `AI_INTERNAL_SERVICE_SECRET` hay `NEXT_PUBLIC_*` credential nào vào mã nguồn frontend.

## Chế độ Mock Preview cục bộ

Để xem trước toàn bộ UI bằng dữ liệu mẫu trong bộ nhớ (không cần backend/PostgreSQL):

```powershell
npm run dev:mock
```

Tài khoản mẫu nằm trong `dev/mocks/fixtures.ts`: học sinh `minh.anh10a1`, giáo viên `ha.nguyen@lumi.example` / `minh.tran@lumi.example`, quản trị viên `nam.pham@lumi.example` (mật khẩu bất kỳ khác rỗng trong chế độ mock). Thanh preview luôn hiển thị rõ; dữ liệu không lưu xuống đĩa hay CSDL và được đặt lại khi tải lại trang.

## Kiểm tra chất lượng & Build Production

```powershell
npm run check
npm run build
npm start
```

`npm run check` chạy lần lượt kiểm tra biên import (`check:boundaries`), kiểm tra kiểu TypeScript (`tsc --noEmit`) và bộ unit/contract tests (`tsx --test`). `npm run build` từ chối `NEXT_PUBLIC_DATA_MODE=mock`, biên dịch bản `live` và quét bundle đầu ra (`check:release`) để bảo đảm không chứa dữ liệu fixture.
