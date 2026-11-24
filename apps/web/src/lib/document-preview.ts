import type { DocumentFormat, DocumentReaderModel } from './domain';

/** Retain the captured source version when opening a saved personal locator. */
export function personalSourceHref(source: { classId: string; documentId: string; version?: number; pageNumber?: number; quote?: string }): string {
  const query = new URLSearchParams();
  if (Number.isSafeInteger(source.version) && source.version! > 0) query.set('version', String(source.version));
  if (Number.isSafeInteger(source.pageNumber) && source.pageNumber! > 0) query.set('page', String(source.pageNumber));
  const cleanQuote = source.quote?.trim().slice(0, 160);
  if (cleanQuote) query.set('quote', cleanQuote);
  const path = `/student/classes/${encodeURIComponent(source.classId)}/documents/${encodeURIComponent(source.documentId)}`;
  return query.size ? `${path}?${query}` : path;
}

export type ClassSourcePreview = {
  documentId: string;
  classId?: string;
  title: string;
  format?: DocumentFormat;
  version: number;
  totalPages: number;
  pages: NonNullable<DocumentReaderModel['pageContents']>;
};
