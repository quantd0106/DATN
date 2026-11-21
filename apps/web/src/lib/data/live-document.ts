import { decodeMutation } from '../api/decoders';
import { ApiError } from '../api/errors';
import type { HttpRequest } from '../api/http';
import { operationPath } from '../api/operations';
import { documentVersionState } from '../document-versions';
import type {
  BookmarkItem, ClassDocumentGrant, ClassSummary, DocumentCapabilityPolicy, DocumentFormat,
  DocumentItem, DocumentReaderModel, DocumentVersion, PersonalNoteItem, ProcessingStatus,
  RouteParams, WorkspaceData,
} from '../domain';
import type { ClassSourcePreview } from '../document-preview';
import type { LiveIdentitySession } from './live-identity';
import type { DataAdapter } from './port';

type ClassReads = {
  list(signal?: AbortSignal): Promise<ClassSummary[]>;
  detail(classId: string, signal?: AbortSignal): Promise<ClassSummary>;
};

function invalid(): never {
  throw new ApiError('CONTRACT_MISMATCH', 'Dữ liệu tài liệu trả về chưa hợp lệ.', 502);
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : invalid();
}

function identifier(value: unknown): string {
  const id = text(value);
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id) ? id.toLowerCase() : invalid();
}

function timestamp(value: unknown): string {
  const date = text(value);
  return /^\d{4}-\d{2}-\d{2}T.+(?:Z|\+00:00)$/.test(date) && Number.isFinite(Date.parse(date)) ? date : invalid();
}

function integer(value: unknown, minimum = 1): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum ? value : invalid();
}

function bool(value: unknown): boolean {
  return typeof value === 'boolean' ? value : invalid();
}

function optional<T>(value: unknown, decode: (value: unknown) => T): T | undefined {
  return value == null ? undefined : decode(value);
}

function list<T>(value: unknown, decode: (item: unknown) => T): T[] {
  return Array.isArray(value) ? value.map(item => decode(item)) : invalid();
}

export function decodeDocumentFormat(value: unknown): DocumentFormat {
  switch (value) {
    case 'MARKDOWN': return 'MD';
    case 'PDF': case 'PPTX': case 'DOCX': case 'TXT': return value;
    default: return invalid();
  }
}

export function decodeProcessingStatus(value: unknown): ProcessingStatus {
  switch (value) {
    case 'SUCCESS': return 'READY';
    case 'PENDING': case 'PROCESSING': case 'NEEDS_REVIEW': case 'FAILED': return value;
    default: return invalid();
  }
}

export function decodeDocumentPolicy(value: unknown): DocumentCapabilityPolicy {
  const dto = record(value);
  if (dto.allowed_formats !== undefined) list(dto.allowed_formats, decodeDocumentFormat);
  return {
    maxSizeBytes: integer(dto.max_size_bytes),
    maxPages: integer(dto.max_pages),
    allowedExtensions: list(dto.allowed_extensions, text),
    allowedMimeTypes: list(dto.allowed_mime_types, text),
  };
}

export function decodeDocumentVersion(value: unknown): DocumentVersion {
  const dto = record(value);
  const size = integer(dto.file_size_bytes);
  return {
    id: identifier(dto.id),
    versionNumber: integer(dto.version_number),
    filename: text(dto.original_filename),
    fileSizeFormatted: size < 1024 * 1024 ? `${(size / 1024).toFixed(1)} KB` : `${(size / (1024 * 1024)).toFixed(1)} MB`,
    uploadedAt: timestamp(dto.created_at),
    status: decodeProcessingStatus(dto.processing_status),
    errorMessage: optional(dto.error_message, text),
    isServing: bool(dto.is_serving),
    isSuperseded: bool(dto.is_superseded),
    reviewSummary: optional(dto.review_summary, value => {
      const summary = record(value);
      return {
        reviewItemCount: integer(summary.review_item_count, 0),
        flagCodes: list(summary.flag_codes, text),
        flaggedPages: list(summary.flagged_pages, integer),
      };
    }),
  };
}

function documentMetadata(dto: Record<string, unknown>, id: unknown) {
  const pageCount = optional(dto.page_count, value => integer(value, 0));
  return {
    id: identifier(id),
    title: text(dto.title),
    description: optional(dto.description, text),
    format: decodeDocumentFormat(dto.format),
    ownerId: identifier(dto.owner_user_id),
    ownerName: optional(dto.owner_display_name, text) ?? 'Giáo viên',
    createdAt: timestamp(dto.created_at),
    updatedAt: timestamp(dto.updated_at),
    pageCount,
    locatorCountLabel: pageCount ? `${pageCount} ${dto.format === 'PPTX' ? 'slide' : 'trang'}` : undefined,
    isBookmarked: optional(dto.is_bookmarked, bool),
  };
}

export function decodeDocumentItem(value: unknown): DocumentItem {
  const dto = record(value);
  return {
    ...documentMetadata(dto, dto.id),
    currentVersion: integer(dto.current_version ?? dto.latest_version_number),
    processingStatus: decodeProcessingStatus(dto.processing_status),
    processingError: optional(dto.error_message, text),
    sharedClassCount: optional(dto.active_grant_count, value => integer(value, 0)),
  };
}

function decodeClassDocument(value: unknown): DocumentItem {
  const dto = record(value);
  return {
    ...documentMetadata(dto, dto.document_id ?? dto.id),
    currentVersion: integer(dto.serving_version ?? dto.serving_version_number),
    processingStatus: 'READY',
  };
}

export function decodeClassDocumentGrant(value: unknown, classes?: ReadonlyMap<string, ClassSummary>): ClassDocumentGrant {
  const dto = record(value);
  const classId = identifier(dto.class_id);
  const klass = classes?.get(classId);
  return {
    classId,
    className: klass?.name ?? optional(dto.class_name, text) ?? 'Lớp học',
    classCode: klass?.code || optional(dto.class_code, text) || '',
    classStatus: klass?.status,
    isShared: bool(dto.is_active),
    grantedAt: optional(dto.granted_at, timestamp),
    servingVersion: integer(dto.serving_version ?? dto.serving_version_number),
    canRevoke: klass?.status === 'ACTIVE' && klass.teachingRole !== undefined,
  };
}

function decodeDocumentDetail(value: unknown, classes?: ReadonlyMap<string, ClassSummary>) {
  const dto = record(value);
  return {
    document: { ...decodeDocumentItem(dto.document), canManageSource: true },
    versions: list(dto.versions, decodeDocumentVersion),
    grants: list(dto.grants, grant => decodeClassDocumentGrant(grant, classes)),
  };
}

function personalSource(dto: Record<string, unknown>, klass?: ClassSummary) {
  const classId = identifier(dto.class_id);
  if (klass && klass.id.toLowerCase() !== classId) return invalid();
  return {
    id: identifier(dto.id),
    userId: optional(dto.user_id, identifier),
    documentId: identifier(dto.document_id),
    documentTitle: text(dto.document_title),
    documentFormat: decodeDocumentFormat(dto.document_format),
    version: optional(dto.version_number, integer),
    classId,
    className: klass?.name ?? optional(dto.class_name, text) ?? 'Lớp học',
    classStatus: klass?.status,
    pageNumber: optional(dto.page_number, integer),
    isSourceAccessible: bool(dto.is_source_accessible),
  };
}

export function decodePersonalNote(value: unknown, klass?: ClassSummary, readerAccessible = true): PersonalNoteItem {
  const dto = record(value);
  const source = personalSource(dto, klass);
  const isSourceAccessible = source.isSourceAccessible && readerAccessible;
  const sourceQuote = optional(dto.source_quote, text);
  return {
    ...source,
    isSourceAccessible,
    locator: text(dto.locator),
    sourceQuote: isSourceAccessible ? sourceQuote : undefined,
    noteText: text(dto.note_text),
    updatedAt: timestamp(dto.updated_at),
  };
}

export function decodeBookmark(value: unknown, klass?: ClassSummary): BookmarkItem {
  const dto = record(value);
  return {
    ...personalSource(dto, klass),
    locator: optional(dto.locator, text) ?? 'Tài liệu',
    createdAt: timestamp(dto.created_at),
  };
}

export function decodeDocumentReader(value: unknown): DocumentReaderModel {
  const dto = record(value);
  const accessible = bool(dto.is_source_accessible);
  const notes = list(dto.notes, note => decodePersonalNote(note, undefined, accessible));
  const document = decodeDocumentItem(dto.document);
  const servingVersion = optional(dto.serving_version, integer);
  const viewingVersion = optional(dto.viewing_version, integer);
  const outline = list(dto.outline, value => {
    const item = record(value);
    return { id: text(item.id), label: text(item.label), pageNumber: integer(item.page_number) };
  });
  const pages = list(dto.pages, value => {
    const page = record(value);
    return {
      pageNumber: integer(page.page_number),
      title: text(page.title),
      paragraphs: list(page.paragraphs, text),
      formulas: optional(page.formulas, formulas => list(formulas, text)),
    };
  });
  const totalPages = optional(dto.total_pages, value => integer(value, 0));
  if (accessible && (!servingVersion || !viewingVersion || (totalPages !== undefined && !totalPages))) return invalid();
  return {
    document: { ...document, isBookmarked: accessible && (optional(dto.is_bookmarked, bool) ?? document.isBookmarked) },
    isSourceAccessible: accessible,
    servingVersion: servingVersion ?? document.currentVersion,
    viewingVersion,
    revokedReason: optional(dto.revoked_reason, text),
    currentPage: 1,
    totalPages: accessible ? totalPages ?? pages.reduce((last, page) => Math.max(last, page.pageNumber), 0) : 0,
    pageContents: accessible ? pages : [],
    outline: accessible ? outline : [],
    notes,
  };
}

function decodeClassSourcePreview(value: unknown, classId: string, documentId: string, version: number): ClassSourcePreview {
  const reader = decodeDocumentReader(value);
  if (!reader.isSourceAccessible) throw new ApiError('FORBIDDEN', 'Bạn hiện không có quyền đọc nguồn tài liệu này.', 403);
  if (reader.document.id !== documentId.toLowerCase() || reader.viewingVersion !== version) return invalid();
  if (!reader.pageContents?.length) throw new ApiError('UNAVAILABLE', 'Nội dung đọc của phiên bản này chưa sẵn sàng.', 503);
  return { classId, documentId, version, title: reader.document.title, totalPages: reader.totalPages, pages: reader.pageContents };
}

async function readPages<T>(
  request: HttpRequest, id: 'documents' | 'classDocuments' | 'myNotes' | 'myBookmarks',
  decode: (value: unknown) => T, signal?: AbortSignal, pathParams?: Record<string, string>,
): Promise<T[]> {
  const items: T[] = [];
  let cursor: string | undefined;
  for (let index = 0; index < 20; index++) {
    const dto = record(await request(id, { cursor, query: { limit: 100 }, signal, pathParams }));
    items.push(...list(dto.items, decode));
    if (dto.next_cursor === null) return items;
    const next = text(dto.next_cursor);
    if (!next || next === cursor) return invalid();
    cursor = next;
  }
  throw new ApiError('PAGE_LIMIT', 'Dữ liệu tài liệu có quá nhiều trang để tải an toàn.', 413);
}

function uploadBody(input: { name: string; file?: Blob }): FormData {
  if (!(input.file instanceof Blob) || !input.file.size) {
    throw new ApiError('VALIDATION_ERROR', 'Vui lòng chọn tệp tải lên có nội dung.', 422);
  }
  const body = new FormData();
  body.append('file', input.file, input.name);
  return body;
}

function downloadFilename(disposition: string | null): string | undefined {
  const encoded = disposition?.match(/(?:^|;)\s*filename\*=utf-8'[^']*'([^;]+)/i)?.[1];
  const plain = disposition?.match(/(?:^|;)\s*filename=(?:"([^"]*)"|([^;]*))/i);
  let filename = plain?.[1] ?? plain?.[2]?.trim();
  if (encoded) {
    try { filename = decodeURIComponent(encoded.trim()); } catch { /* Use the plain filename if present. */ }
  }
  return filename?.split(/[/\\]/).pop()?.replace(/[\u0000-\u001f\u007f]/g, '') || undefined;
}

function routeVersion(value: string | undefined): number | undefined {
  if (!value || !/^\d+$/.test(value)) return undefined;
  const version = Number(value);
  return Number.isSafeInteger(version) && version > 0 ? version : undefined;
}

export function createLiveDocument(session: LiveIdentitySession, classes: ClassReads) {
  const document: DataAdapter['document'] = {
    async uploadDocument(input) {
      const body = uploadBody(input);
      body.set('title', input.title);
      if (input.description !== undefined) body.set('description', input.description);
      return session.authenticated(async request => decodeMutation(decodeDocumentItem, await request('createDocument', { body })), true);
    },
    async uploadVersion(documentId, input) {
      const body = uploadBody(input);
      return session.authenticated(async request => decodeMutation(
        decodeDocumentVersion, await request('uploadDocumentVersion', { pathParams: { documentId }, body }),
      ), true);
    },
    async acceptVersion(documentId, versionId) {
      return session.authenticated(async request => decodeMutation(
        decodeDocumentVersion, await request('acceptDocumentVersion', { pathParams: { documentId, versionId } }),
      ), true);
    },
    async retryProcessing(documentId, versionNumber, versionId) {
      return session.authenticated(async request => {
        if (!versionId) {
          const detail = decodeDocumentDetail(await request('documentDetail', { pathParams: { documentId } }));
          const { latest, ordered } = documentVersionState(detail.versions);
          const target = versionNumber === undefined ? latest : ordered.find(version => version.versionNumber === versionNumber);
          if (!target?.id) throw new ApiError('NOT_FOUND', 'Không tìm thấy phiên bản tài liệu.', 404);
          versionId = target.id;
        }
        decodeMutation(decodeDocumentVersion, await request('retryDocumentVersion', { pathParams: { documentId, versionId } }));
        try {
          return decodeDocumentDetail(await request('documentDetail', { pathParams: { documentId } })).document;
        } catch (error) {
          if (error instanceof ApiError) error.outcome = 'unknown';
          throw error;
        }
      }, true);
    },
    async shareDocument(documentId, classIds) {
      const targets = [...new Set(classIds)];
      if (!targets.length) throw new ApiError('VALIDATION_ERROR', 'Vui lòng chọn ít nhất một lớp để chia sẻ.', 422);
      operationPath('documentDetail', { documentId });
      for (const classId of targets) operationPath('grantClassDocument', { classId });
      await session.authenticated(async request => {
        const results = await Promise.allSettled(targets.map(async classId => {
          decodeMutation(decodeClassDocumentGrant, await request('grantClassDocument', {
            pathParams: { classId }, body: { document_id: documentId },
          }));
        }));
        const rejected = results.filter(result => result.status === 'rejected');
        const uncertain = rejected.find(({ reason }) => !(reason instanceof ApiError) || reason.outcome === 'unknown');
        const failure = uncertain ?? rejected[0];
        if (failure) throw failure.reason;
      }, true);
    },
    async revokeShare(documentId, classId) {
      await session.authenticated(request => request('revokeClassDocument', { pathParams: { documentId, classId } }), true);
    },
    async deleteDocument(documentId, options) {
      const query = options?.confirmMultiClass ? { confirm_multi_class: 'true' } : undefined;
      await session.authenticated(request => request('deleteDocument', { pathParams: { documentId }, query }), true);
    },
    async saveNote(input) {
      return session.authenticated(async request => {
        const response = input.id
          ? await request('updateNote', { pathParams: { noteId: input.id }, body: { note_text: input.noteText } })
          : await request('createClassNote', { pathParams: { classId: input.classId }, body: {
              document_id: input.documentId, version_number: input.version, locator: input.locator,
              page_number: input.pageNumber, note_text: input.noteText, source_quote: input.sourceQuote,
            } });
        return decodeMutation(decodePersonalNote, response);
      }, true);
    },
    async deleteNote(noteId) {
      await session.authenticated(request => request('deleteNote', { pathParams: { noteId } }), true);
    },
    async toggleBookmark(documentId, classId, locator, version, pageNumber) {
      return session.authenticated(async request => decodeMutation(value => {
        const dto = record(value);
        const bookmarked = bool(dto.bookmarked);
        if (bookmarked) decodeBookmark(dto.bookmark);
        else if (dto.bookmark !== null) return invalid();
        return { bookmarked };
      }, await request('toggleClassBookmark', {
        pathParams: { classId }, body: { document_id: documentId, version_number: version, locator, page_number: pageNumber },
      })), true);
    },
    async previewClassSource({ classId, documentId, version }) {
      return session.authenticated(async request => decodeClassSourcePreview(await request('classDocumentReader', {
        pathParams: { classId, documentId }, query: { version },
      }), classId, documentId, version));
    },
    async downloadClassDocument(classId, documentId, version) {
      return session.authenticated(async request => {
        let filename: string | undefined;
        const blob = await request('classDocumentDownload', {
          pathParams: { classId, documentId }, query: { version },
          onResponse: response => { filename = downloadFilename(response.headers.get('content-disposition')); },
        });
        return { blob, filename };
      });
    },
  };

  function loadWorkspace(routeId: string, params: RouteParams, signal?: AbortSignal): Promise<WorkspaceData> | undefined {
    const { classId, documentId } = params;
    switch (routeId) {
      case 't.library':
        return session.authenticated(async request => {
          const [capabilityPolicy, libraryDocuments] = await Promise.all([
            request('documentPolicy', { signal }).then(decodeDocumentPolicy),
            readPages(request, 'documents', decodeDocumentItem, signal),
          ]);
          return { kind: 'ready', capabilityPolicy, libraryDocuments };
        });
      case 't.libraryDocument':
        return session.authenticated(async request => {
          const [capabilityPolicy, detail, classList] = await Promise.all([
            request('documentPolicy', { signal }).then(decodeDocumentPolicy),
            request('documentDetail', { pathParams: { documentId }, signal }),
            classes.list(signal),
          ]);
          const byId = new Map(classList.map(klass => [klass.id.toLowerCase(), klass]));
          const shareableClasses = classList.filter(klass => klass.status === 'ACTIVE' && klass.teachingRole !== undefined);
          const sourceDocumentDetail = decodeDocumentDetail(detail, byId);
          if (sourceDocumentDetail.document.id !== documentId.toLowerCase()) return invalid();
          return { kind: 'ready', capabilityPolicy, sourceDocumentDetail: { ...sourceDocumentDetail, shareableClasses } };
        });
      case 't.documents':
      case 's.documents':
        return session.authenticated(async request => {
          const [classSummary, documents] = await Promise.all([
            classes.detail(classId, signal),
            readPages(request, 'classDocuments', decodeClassDocument, signal, { classId }),
          ]);
          if (classSummary.id.toLowerCase() !== classId.toLowerCase()) return invalid();
          const viewer = session.currentUser();
          const teacher = routeId === 't.documents' && viewer?.roles.includes('LECTURER') === true;
          const classDocuments = documents.map(item => ({
            ...item,
            canManageSource: teacher && viewer?.id.toLowerCase() === item.ownerId,
            canRevokeGrant: teacher && classSummary.status === 'ACTIVE' && classSummary.teachingRole !== undefined,
          }));
          return { kind: 'ready', classSummary, classDocuments };
        });
      case 's.reader':
        return session.authenticated(async request => {
          const readerModel = decodeDocumentReader(await request('classDocumentReader', {
            pathParams: { classId, documentId }, query: { version: routeVersion(params.version) }, signal,
          }));
          const requestedDocument = documentId.toLowerCase(), requestedClass = classId.toLowerCase();
          if (readerModel.document.id !== requestedDocument || readerModel.notes.some(note => note.documentId !== requestedDocument || note.classId !== requestedClass)) return invalid();
          return { kind: 'ready', readerModel };
        });
      case 's.notes':
        return session.authenticated(async request => {
          const [notes, bookmarks, classList] = await Promise.all([
            readPages(request, 'myNotes', record, signal),
            readPages(request, 'myBookmarks', record, signal),
            classes.list(signal),
          ]);
          const byId = new Map(classList.map(klass => [klass.id.toLowerCase(), klass]));
          return {
            kind: 'ready',
            studentNotes: notes.map(note => decodePersonalNote(note, byId.get(identifier(note.class_id)))),
            studentBookmarks: bookmarks.map(bookmark => decodeBookmark(bookmark, byId.get(identifier(bookmark.class_id)))),
          };
        });
      default:
        return undefined;
    }
  }

  return { document, loadWorkspace };
}
