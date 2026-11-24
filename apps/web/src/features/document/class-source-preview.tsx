'use client';
import { useEffect, useState } from 'react';
import { adapter } from '@/lib/data';
import type { ClassSourcePreview } from '@/lib/document-preview';
import { Dialog, StatePanel } from '@/components/ui';
import { MathFormula, RichMathText } from './reader-content';
import { OriginalPdfViewer } from './components/original-pdf-viewer';
import './class-source-preview.css';

export type SourcePreviewRequest =
  | { scope?: 'class'; classId: string; documentId: string; version: number; page?: number; title?: string }
  | { scope: 'library'; documentId: string; version?: number; page?: number; title?: string };

export function ClassSourcePreviewDialog({
  request,
  onClose,
}: {
  request: SourcePreviewRequest | null;
  onClose(): void;
}) {
  const [snapshot, setSnapshot] = useState<{ key: string; data?: ClassSourcePreview; error?: string } | null>(null);
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState(0);
  const [viewMode, setViewMode] = useState<'text' | 'pdf'>('text');
  const key = request ? JSON.stringify(request) : '';

  useEffect(() => {
    let active = true;
    if (!request) {
      setSnapshot(null);
      return;
    }
    setSnapshot(null);
    setPage(request.page ?? 1);
    setViewMode('text');
    const promise =
      request.scope === 'library'
        ? adapter.document.previewLibraryDocument({ documentId: request.documentId, version: request.version })
        : adapter.document.previewClassSource({
            classId: request.classId,
            documentId: request.documentId,
            version: request.version,
          });
    void promise
      .then((data) => {
        if (active) setSnapshot({ key, data });
      })
      .catch((error) => {
        if (active)
          setSnapshot({
            key,
            error: error instanceof Error ? error.message : 'Chưa thể mở nguồn tài liệu.',
          });
      });
    return () => {
      active = false;
    };
  }, [key, retry]);

  const current = snapshot?.key === key ? snapshot : null;
  const content = current?.data?.pages.find((p) => p.pageNumber === page) ?? current?.data?.pages[0];
  const isPdf = current?.data?.format === 'PDF';
  const dialogTitle = request?.scope === 'library' ? 'Xem trước tài liệu thư viện' : 'Đọc nguồn tài liệu của lớp';

  return (
    <Dialog open={Boolean(request)} title={dialogTitle} onClose={onClose} className="class-source-preview">
      {!current ? (
        <StatePanel busy title="Đang kiểm tra quyền đọc và phiên bản…" />
      ) : current.error ? (
        <StatePanel title="Nguồn tài liệu chưa khả dụng" message={current.error}>
          <button className="button" onClick={() => setRetry((r) => r + 1)}>
            Kiểm tra lại
          </button>
        </StatePanel>
      ) : current.data && request ? (
        <>
          <div className="source-preview-topbar">
            <p>
              <strong>{current.data.title}</strong> · Phiên bản {current.data.version}
            </p>
            {isPdf && (
              <div className="reader-view-switch" role="group" aria-label="Chế độ xem tài liệu">
                <button
                  type="button"
                  className={viewMode === 'text' ? 'is-active' : undefined}
                  onClick={() => setViewMode('text')}
                >
                  Bản trích xuất
                </button>
                <button
                  type="button"
                  className={viewMode === 'pdf' ? 'is-active' : undefined}
                  onClick={() => setViewMode('pdf')}
                >
                  PDF gốc
                </button>
              </div>
            )}
          </div>
          <label>
            Trang{' '}
            <select className="input" value={page} onChange={(e) => setPage(Number(e.target.value))}>
              {current.data.pages.map((p) => (
                <option key={p.pageNumber} value={p.pageNumber}>
                  {p.pageNumber} · {p.title}
                </option>
              ))}
            </select>
          </label>
          {viewMode === 'pdf' && isPdf ? (
            <OriginalPdfViewer
              cacheKey={`${request.scope ?? 'class'}:${current.data.documentId}:${current.data.version}`}
              title={current.data.title}
              pageNumber={page}
              loadPdf={() =>
                request.scope === 'library'
                  ? adapter.document.downloadLibraryDocument(current.data!.documentId, current.data!.version)
                  : adapter.document.downloadClassDocument(request.classId, current.data!.documentId, current.data!.version)
              }
            />
          ) : content ? (
            <article className="card source-reading-page">
              <h3>{content.title}</h3>
              {content.paragraphs.map((text, i) => (
                <p key={i}>
                  <RichMathText text={text} />
                </p>
              ))}
              {content.formulas?.map((formula, i) => (
                <div className="source-formula" key={i}>
                  <MathFormula value={formula} />
                </div>
              ))}
            </article>
          ) : (
            <StatePanel title="Không có nội dung ở vị trí này" message="Chọn một trang có trong phiên bản tài liệu." />
          )}
          <p className="muted">
            {request.scope === 'library'
              ? 'Xem trước nội dung đã trích xuất và tệp PDF gốc thuộc thư viện của bạn.'
              : 'Nguồn được chia sẻ để đọc trong lớp. Việc quản lý tài liệu thuộc thư viện của chủ sở hữu.'}
          </p>
        </>
      ) : null}
    </Dialog>
  );
}
