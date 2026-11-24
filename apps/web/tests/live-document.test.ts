import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError } from '../src/lib/api/errors';
import {
  createLiveDocument, decodeBookmark, decodeDocumentItem, decodeDocumentPolicy,
  decodeDocumentReader, decodeDocumentVersion, decodePersonalNote,
} from '../src/lib/data/live-document';
import { createLiveCore } from '../src/lib/data/live-core';
import { createLiveIdentitySession } from '../src/lib/data/live-identity';

const id = '00000000-0000-4000-8000-000000000001';
const owner = '00000000-0000-4000-8000-000000000002';
const versionId = '00000000-0000-4000-8000-000000000003';
const classId = '00000000-0000-4000-8000-000000000004';
const noteId = '00000000-0000-4000-8000-000000000005';
const otherClass = '00000000-0000-4000-8000-000000000006';
const created = '2026-11-21T10:00:00Z';
const document = {
  id, title: 'Mẫu Markdown', description: null, format: 'MARKDOWN', current_version: 2, processing_status: 'SUCCESS',
  owner_user_id: owner, owner_display_name: 'Giáo viên A', created_at: created, updated_at: created, page_count: 1,
};
const version = {
  id: versionId, version_number: 2, original_filename: 'source.md', file_size_bytes: 10,
  created_at: created, processing_status: 'SUCCESS', is_serving: true, is_superseded: false,
};
const note = {
  id: noteId, class_id: classId, document_id: id, document_title: document.title, document_format: 'MARKDOWN',
  version_number: 2, locator: 'Trang 1', page_number: 1, note_text: 'Ghi chú của tôi',
  updated_at: created, source_quote: null, is_source_accessible: false,
};
const policy = { max_size_bytes: 20971520, max_pages: 300, allowed_extensions: ['.md'], allowed_mime_types: ['text/markdown'] };
const klass = { id: classId, name: 'Toán', code: 'TOAN-CORE', grade_level: 9, enrollment_mode: 'MANUAL', status: 'ACTIVE', teaching_role: 'ASSISTING' };
const grant = { class_id: classId, document_id: id, is_active: true, serving_version: 2, granted_at: created, class_code: 'TOAN' };
const detail = { document, versions: [version], grants: [grant] };
const reader = {
  document, serving_version: 2, viewing_version: 2, is_source_accessible: true, total_pages: 1,
  outline: [{ id: 'outline-1', label: 'Mở đầu', page_number: 1 }],
  pages: [{ page_number: 1, title: 'Mở đầu', paragraphs: ['Nội dung'] }], notes: [],
};
const page = (items: unknown[], next_cursor: string | null = null) => ({ items, next_cursor });

function liveFor(fetcher: typeof fetch) {
  const session = createLiveIdentitySession({
    fetcher: async (input, init) => {
      if (input === '/api/v1/auth/refresh') return Response.json({ access_token: 'opaque', token_type: 'bearer', expires_in: 60 });
      if (input === '/api/v1/users/me') return Response.json({
        id: owner, email: null, username: null, school_id: null, display_name: null, roles: ['LECTURER'], status: 'ACTIVE',
      });
      assert.equal(new Headers(init?.headers).get('authorization'), 'Bearer opaque');
      return fetcher(input, init);
    },
  });
  return { session, ...createLiveDocument(session, createLiveCore(session).classroomReads) };
}

test('Document decoders map wire enums, version flags and the upload policy', () => {
  assert.equal(decodeDocumentItem(document).format, 'MD');
  assert.equal(decodeDocumentVersion(version).status, 'READY');
  assert.equal(decodeDocumentVersion({ ...version, is_serving: false, is_superseded: true }).isSuperseded, true);
  const review = decodeDocumentVersion({
    ...version, processing_status: 'NEEDS_REVIEW', is_serving: false,
    review_summary: { review_item_count: 2, flag_codes: ['FAST_OCR_REVIEW'], flagged_pages: [1, 2] },
  });
  assert.equal(review.id, versionId);
  assert.equal(review.status, 'NEEDS_REVIEW');
  assert.deepEqual(review.reviewSummary, { reviewItemCount: 2, flagCodes: ['FAST_OCR_REVIEW'], flaggedPages: [1, 2] });
  assert.deepEqual(decodeDocumentPolicy(policy), { maxSizeBytes: 20971520, maxPages: 300, allowedExtensions: ['.md'], allowedMimeTypes: ['text/markdown'] });
  for (const badVersion of [
    { ...version, id: 'not-a-uuid' }, { ...version, is_superseded: 'false' },
    { ...version, processing_status: 'SUPERSEDED' }, { ...version, file_size_bytes: 0 },
  ]) assert.throws(() => decodeDocumentVersion(badVersion), { code: 'CONTRACT_MISMATCH' });
  assert.throws(() => decodeDocumentPolicy({ ...policy, max_pages: '300' }), { code: 'CONTRACT_MISMATCH' });
});

test('revoked reader redacts source text and preserves the owner’s personal note', () => {
  const decoded = decodePersonalNote(note);
  assert.equal(decoded.isSourceAccessible, false);
  assert.equal(decoded.sourceQuote, undefined);
  assert.equal(decoded.noteText, note.note_text);
  assert.equal(decodeBookmark({ ...note, created_at: created }).isSourceAccessible, false);
  const revoked = decodeDocumentReader({
    ...reader, is_source_accessible: false, serving_version: null, viewing_version: null,
    notes: [{ ...note, is_source_accessible: true, source_quote: 'Private source text' }],
  });
  assert.deepEqual(revoked.outline, []);
  assert.deepEqual(revoked.pageContents, []);
  assert.equal(revoked.totalPages, 0);
  assert.equal(revoked.notes[0].isSourceAccessible, false);
  assert.equal(revoked.notes[0].sourceQuote, undefined);
  assert.equal(revoked.notes[0].noteText, note.note_text);
  assert.equal(decodePersonalNote({ ...note, is_source_accessible: true, source_quote: 'Visible' }).sourceQuote, 'Visible');
});

test('live uploads transmit file bytes and accept explicitly by version UUID', async () => {
  const bytes = new Uint8Array([0x80, 0xff, 0x00]);
  const file = new File([bytes], 'source.md', { type: 'text/markdown' });
  const calls: string[] = [];
  const adapter = liveFor(async (path, init) => {
    calls.push(String(path));
    assert.equal(init?.method, 'POST');
    if (String(path).endsWith('/accept')) {
      assert.equal(init?.body, undefined);
      return Response.json(version);
    }
    assert.ok(init?.body instanceof FormData);
    assert.equal(new Headers(init?.headers).has('content-type'), false);
    const upload = init.body.get('file');
    assert.ok(upload instanceof File);
    assert.equal(upload.name, 'source.md');
    assert.deepEqual(new Uint8Array(await upload.arrayBuffer()), bytes);
    if (String(path).endsWith('/versions')) {
      assert.equal(init.body.has('title'), false);
      return Response.json({ ...version, processing_status: 'PENDING', is_serving: false }, { status: 201 });
    }
    assert.equal(init.body.get('title'), document.title);
    assert.equal(init.body.get('description'), 'Mô tả');
    return Response.json(document, { status: 201 });
  }).document;
  const input = { file, name: file.name, size: file.size, type: file.type };
  assert.equal((await adapter.uploadDocument({ ...input, title: document.title, description: 'Mô tả' })).format, 'MD');
  assert.equal((await adapter.uploadVersion(id, input)).status, 'PENDING');
  assert.equal((await adapter.acceptVersion(id, versionId)).status, 'READY');
  assert.deepEqual(calls, ['/api/v1/documents', `/api/v1/documents/${id}/versions`, `/api/v1/documents/${id}/versions/${versionId}/accept`]);
});

test('live workspaces stitch Document DTOs to current Core classroom context', async () => {
  const readerQueries: (string | null)[] = [];
  const live = liveFor(async input => {
    const path = new URL(String(input), 'http://web');
    switch (path.pathname) {
      case '/api/v1/documents/policy': return Response.json(policy);
      case '/api/v1/documents': return Response.json(path.searchParams.has('cursor') ? page([]) : page([document], 'next'));
      case `/api/v1/documents/${id}`: return Response.json(detail);
      case '/api/v1/classes': return Response.json(page([klass, { ...klass, id: otherClass, status: 'ARCHIVED' }]));
      case `/api/v1/classes/${classId}`: return Response.json(klass);
      case `/api/v1/classes/${classId}/documents`: return Response.json(page([{ ...document, serving_version: 2 }]));
      case `/api/v1/classes/${classId}/documents/${id}/reader`:
        readerQueries.push(path.searchParams.get('version'));
        return Response.json(reader);
      case '/api/v1/users/me/notes': return Response.json(page([note]));
      case '/api/v1/users/me/bookmarks': return Response.json(page([{ ...note, created_at: created }]));
      default: throw new Error(`Unexpected request ${path.pathname}`);
    }
  });
  await live.session.identity.restore();
  const library = await live.loadWorkspace('t.library', {});
  assert.equal(library?.kind === 'ready' && library.libraryDocuments?.length, 1);
  const source = await live.loadWorkspace('t.libraryDocument', { documentId: id });
  assert.ok(source?.kind === 'ready' && source.sourceDocumentDetail);
  assert.deepEqual(source.sourceDocumentDetail.shareableClasses?.map(item => item.id), [classId]);
  assert.equal(source.sourceDocumentDetail.grants[0].className, klass.name);
  assert.equal(source.sourceDocumentDetail.grants[0].classStatus, 'ACTIVE');
  assert.equal(source.sourceDocumentDetail.grants[0].classCode, klass.code);
  for (const route of ['t.documents', 's.documents']) {
    const data = await live.loadWorkspace(route, { classId });
    assert.equal(data?.kind === 'ready' && data.classSummary?.id, classId);
    assert.equal(data?.kind === 'ready' && data.classDocuments?.[0].canRevokeGrant, route === 't.documents');
  }
  const notes = await live.loadWorkspace('s.notes', {});
  assert.ok(notes?.kind === 'ready');
  assert.equal(notes.studentNotes?.[0].className, klass.name);
  assert.equal(notes.studentBookmarks?.[0].classStatus, 'ACTIVE');
  const opened = await live.loadWorkspace('s.reader', { classId, documentId: id, version: '2' });
  assert.equal(opened?.kind === 'ready' && opened.readerModel?.pageContents?.[0].title, 'Mở đầu');
  await live.loadWorkspace('s.reader', { classId, documentId: id, version: '2.5' });
  const preview = await live.document.previewClassSource({ classId, documentId: id, version: 2 });
  assert.equal(preview.version, 2);
  assert.equal(preview.format, 'MD');
  assert.deepEqual(readerQueries, ['2', null, '2']);
  assert.equal(live.loadWorkspace('t.feed', { classId }), undefined);
});

test('live library preview allows nullable serving_version for unshared or NEEDS_REVIEW owner documents', async () => {
  const bytes = new Uint8Array([0x25, 0x50, 0x44, 0x46]);
  const live = liveFor(async (path, init) => {
    const url = String(path);
    if (url.endsWith(`/documents/${id}/reader`) || url.endsWith(`/documents/${id}/reader?version=1`)) {
      return Response.json({
        document: { ...document, format: 'PDF', processing_status: 'NEEDS_REVIEW', current_version: 1 },
        is_source_accessible: true,
        serving_version: null,
        viewing_version: 1,
        revoked_reason: null,
        total_pages: 1,
        outline: [],
        pages: [{ page_number: 1, title: 'Trang 1', paragraphs: ['Nội dung cần rà soát'], formulas: ['x^2 = 4'] }],
        notes: [],
        is_bookmarked: false,
      });
    }
    if (url.endsWith(`/documents/${id}/download?version=1`)) {
      assert.equal(new Headers(init?.headers).get('accept'), '*/*');
      return new Response(bytes, {
        headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="library.pdf"' },
      });
    }
    return new Response(null, { status: 404 });
  });
  await live.session.identity.restore();
  const preview = await live.document.previewLibraryDocument({ documentId: id, version: 1 });
  assert.equal(preview.version, 1);
  assert.equal(preview.format, 'PDF');
  assert.equal(preview.pages[0].paragraphs[0], 'Nội dung cần rà soát');
  const downloaded = await live.document.downloadLibraryDocument(id, 1);
  assert.equal(downloaded.filename, 'library.pdf');
  assert.deepEqual(new Uint8Array(await downloaded.blob.arrayBuffer()), bytes);
});

test('live Document mutations use the contracted paths, confirm flags and narrow note edits', async () => {
  const calls: string[] = [];
  const adapter = liveFor(async (input, init) => {
    const path = String(input);
    calls.push(`${init?.method} ${path}`);
    if (path.endsWith('/grants')) {
      assert.deepEqual(JSON.parse(String(init?.body)), { document_id: id });
      return Response.json(grant, { status: 200 });
    }
    if (path.endsWith('/retry')) return Response.json({ ...version, processing_status: 'PENDING' });
    if (init?.method === 'GET' && path.startsWith('/api/v1/documents/')) return Response.json(detail);
    if (init?.method === 'PATCH') {
      assert.deepEqual(JSON.parse(String(init.body)), { note_text: 'Đã sửa' });
      return Response.json(note);
    }
    if (path.endsWith('/notes')) {
      assert.deepEqual(JSON.parse(String(init?.body)), { document_id: id, version_number: 2, locator: 'Trang 1', page_number: 1, note_text: 'Đã sửa' });
      return Response.json(note, { status: 201 });
    }
    if (path.endsWith('/toggle')) return Response.json({ bookmarked: false, bookmark: null });
    return new Response(null, { status: 204 });
  }).document;
  await adapter.shareDocument(id, [classId, classId]);
  await adapter.revokeShare(id, classId);
  await adapter.retryProcessing(id, 2);
  const edit = { documentId: id, classId, locator: 'Trang 1', version: 2, pageNumber: 1, noteText: 'Đã sửa' };
  await adapter.saveNote(edit);
  await adapter.saveNote({ ...edit, id: noteId });
  await adapter.deleteNote(noteId);
  await adapter.toggleBookmark(id, classId, edit.locator, edit.version, edit.pageNumber);
  await adapter.deleteDocument(id, { confirmMultiClass: true });
  assert.deepEqual(calls, [
    `POST /api/v1/classes/${classId}/documents/grants`, `DELETE /api/v1/classes/${classId}/documents/grants/${id}`,
    `GET /api/v1/documents/${id}`, `POST /api/v1/documents/${id}/versions/${versionId}/retry`, `GET /api/v1/documents/${id}`,
    `POST /api/v1/classes/${classId}/notes`, `PATCH /api/v1/notes/${noteId}`, `DELETE /api/v1/notes/${noteId}`,
    `POST /api/v1/classes/${classId}/bookmarks/toggle`, `DELETE /api/v1/documents/${id}?confirm_multi_class=true`,
  ]);
});

test('live downloads preserve the filename and bytes and surface Document authorization failures', async () => {
  const bytes = new Uint8Array([0x80, 0xff, 0]);
  const live = liveFor(async (path, init) => {
    assert.equal(new Headers(init?.headers).get('accept'), '*/*');
    assert.match(String(path), /\/download\?version=2$/);
    return new Response(bytes, { headers: { 'Content-Disposition': "attachment; filename*=UTF-8''t%C3%A0i-li%E1%BB%87u.md" } });
  });
  const downloaded = await live.document.downloadClassDocument(classId, id, 2);
  assert.equal(downloaded.filename, 'tài-liệu.md');
  assert.deepEqual(new Uint8Array(await downloaded.blob.arrayBuffer()), bytes);
  const denied = liveFor(async () => Response.json({ error: { code: 'FORBIDDEN' } }, { status: 403 }));
  await assert.rejects(() => denied.document.downloadClassDocument(classId, id), (error: unknown) => error instanceof ApiError && error.status === 403);
});
