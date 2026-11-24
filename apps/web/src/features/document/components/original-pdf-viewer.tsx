'use client';

import { useEffect, useState } from 'react';
import { Icon, StatePanel } from '@/components/ui';
import { errorMessage } from '@/lib/api/errors';

type Props = {
  cacheKey: string;
  title: string;
  pageNumber?: number;
  loadPdf: () => Promise<{ blob: Blob; filename?: string }>;
  onDownload?: () => void;
};

export function OriginalPdfViewer({ cacheKey, title, pageNumber, loadPdf, onDownload }: Props) {
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [filename, setFilename] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryTick, setRetryTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setLoading(true);
    setError(null);
    setPdfUrl(null);
    setFilename(undefined);

    void loadPdf()
      .then((result) => {
        if (cancelled) return;
        const pdfBlob =
          result.blob.type === 'application/pdf'
            ? result.blob
            : new Blob([result.blob], { type: 'application/pdf' });
        objectUrl = URL.createObjectURL(pdfBlob);
        setPdfUrl(objectUrl);
        setFilename(result.filename);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // Re-fetch only when document/version cacheKey or manual retry changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cacheKey, retryTick]);

  const targetSrc = pdfUrl
    ? pageNumber && pageNumber > 0
      ? `${pdfUrl}#page=${pageNumber}`
      : pdfUrl
    : null;

  function triggerDownload() {
    if (onDownload) {
      onDownload();
      return;
    }
    if (!pdfUrl) return;
    const anchor = document.createElement('a');
    anchor.href = pdfUrl;
    anchor.download = filename ?? `${title}.pdf`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  }

  return (
    <div className="original-pdf-viewer">
      <div className="original-pdf-toolbar">
        <span className="muted text-xs">
          Bản PDF gốc{pageNumber && pageNumber > 0 ? ` · Trang ${pageNumber}` : ''}
        </span>
        <div className="original-pdf-actions">
          {targetSrc && (
            <a
              href={targetSrc}
              target="_blank"
              rel="noopener noreferrer"
              className="button secondary small"
            >
              Mở ở tab mới
            </a>
          )}
          <button
            type="button"
            className="button secondary small"
            disabled={!pdfUrl && !onDownload}
            onClick={triggerDownload}
          >
            <Icon name="download" />
            <span>Tải PDF gốc</span>
          </button>
        </div>
      </div>

      {loading && <StatePanel busy title="Đang tải tệp PDF gốc…" />}

      {!loading && error && (
        <StatePanel title="Chưa thể hiển thị PDF gốc" message={error}>
          <button
            type="button"
            className="button primary small"
            onClick={() => setRetryTick((tick) => tick + 1)}
          >
            Thử lại
          </button>
        </StatePanel>
      )}

      {!loading && !error && targetSrc && (
        <iframe
          key={targetSrc}
          className="original-pdf-frame"
          src={targetSrc}
          title={`PDF gốc: ${title}`}
        />
      )}
    </div>
  );
}
