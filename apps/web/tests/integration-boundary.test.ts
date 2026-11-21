import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { assertDataSource, resolveDataMode } from '../config/data-mode.mjs';
import configure from '../next.config.mjs';
import { inspectSource } from '../scripts/boundary-policy.mjs';
import { findFixtureLeaks } from '../scripts/release-policy.mjs';
import { createHttpClient } from '../src/lib/api/http';
import { createCoreProxy } from '../src/lib/api/proxy';
import { ApiError } from '../src/lib/api/errors';
import { decodeMutation, decodeUser } from '../src/lib/api/decoders';
import { findOperation, isImplementedOperation, operationPath } from '../src/lib/api/operations';
import { safeReturnTo } from '../src/lib/navigation';

test('production rejects mock, including actual Next config', () => {
  assert.equal(resolveDataMode(undefined, true), 'live');
  assert.throws(() => resolveDataMode('mock', true));
  assert.throws(() => resolveDataMode('typo', false));
  const previous = process.env.NEXT_PUBLIC_DATA_MODE;
  try { process.env.NEXT_PUBLIC_DATA_MODE = 'mock'; assert.throws(() => configure('phase-production-build')); }
  finally { if (previous === undefined) delete process.env.NEXT_PUBLIC_DATA_MODE; else process.env.NEXT_PUBLIC_DATA_MODE = previous; }
});
test('boundary analysis rejects fixture imports, bypasses and persisted credentials', () => {
  assert.ok(inspectSource('src/features/example.ts', "import { users } from '../../dev/mocks/fixtures';").length);
  assert.ok(inspectSource('app/example/page.tsx', "fetch('/api/v1/users/me');").length);
  assert.ok(inspectSource('src/features/example.ts', "window.localStorage.setItem('access', token);").length);
  assert.ok(inspectSource('src/features/example.ts', "import { adapter } from '@data-adapter';").length);
  assert.ok(inspectSource('src/features/example.ts', "require('../../dev/mocks/fixtures');").length);
  assert.ok(inspectSource('src/features/example.ts', "import(variablePath);").length);
  assert.equal(inspectSource('src/features/example.ts', "import { adapter } from '@/lib/data';").length, 0);
});
test('adapter provenance cannot silently differ from selected live mode', () => {
  assert.throws(() => assertDataSource('live', 'mock', false));
  assert.throws(() => assertDataSource('mock', 'mock', true));
  assert.doesNotThrow(() => assertDataSource('live', 'live', true));
});
test('schema mismatch after a successful mutation is an unknown outcome', () => {
  assert.throws(() => decodeMutation(decodeUser, { unexpected: true }), (e: unknown) => e instanceof ApiError && e.outcome === 'unknown');
});
test('release scanner detects a fixture leak, not just a source filename', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'lumi-release-'));
  try { await writeFile(join(dir, 'chunk.js'), 'const data="__LUMI_PREVIEW_FIXTURE_CANARY_20261018__";'); assert.equal((await findFixtureLeaks(dir)).length, 1); }
  finally { await rm(dir, { recursive: true, force: true }); }
});
test('transport sends exact login credentials without generic idempotency, includes cookie, no cache', async () => {
  let call: { url: string; options?: RequestInit } | null = null;
  const client = createHttpClient(() => null, (async (url, options) => { call = { url: String(url), options }; return Response.json({ ok: true }); }) as typeof fetch);
  await client('login', { body: { username: 'user', password: 'password' } });
  assert.equal(call!.url, '/api/v1/auth/login');
  assert.equal(call!.options!.credentials, 'include'); assert.equal(call!.options!.cache, 'no-store');
  assert.equal(new Headers(call!.options!.headers).has('Idempotency-Key'), false);
  assert.deepEqual(JSON.parse(String(call!.options!.body)), { username: 'user', password: 'password' });
});
test('network failure after mutation remains unknown, never retries or returns fixture success', async () => {
  let calls = 0; const client = createHttpClient(() => null, (async () => { calls++; throw new TypeError('offline'); }) as typeof fetch);
  await assert.rejects(() => client('createStudent', { body: { username: 'user' } }), (e: unknown) => e instanceof ApiError && e.outcome === 'unknown');
  assert.equal(calls, 1);
  await assert.rejects(() => client('me'), (e: unknown) => e instanceof ApiError && e.outcome === 'rejected');
});
test('invalid live response never becomes preview user data', () => {
  assert.throws(() => decodeUser({ id: 'x', roles: ['OWNER'], status: 'ACTIVE' }), ApiError);
});
test('unexpected successful HTTP status is not a confirmed mutation', async () => {
  const client = createHttpClient(() => null, (async () => Response.json({})) as typeof fetch);
  await assert.rejects(() => client('activate', { body: {} }), (e: unknown) => e instanceof ApiError && e.outcome === 'unknown');
});
test('configured public origin works behind an internal proxy URL', async () => {
  const proxy = createCoreProxy(() => 'http://core:8000', (async () => Response.json({})) as typeof fetch, () => 'https://school.example');
  const request = new Request('http://internal-next:3000/api/v1/auth/login', { method: 'POST', headers: { Origin: 'https://school.example' }, body: '{}' });
  assert.equal((await proxy(request, ['auth','login'])).status, 200);
  const missing = createCoreProxy(() => 'http://core:8000', fetch, () => undefined);
  assert.equal((await missing(request, ['auth','login'])).status, 503);
});
test('only implemented public Core operations enter the proxy', () => {
  assert.equal(isImplementedOperation('POST', '/auth/login'), true);
  assert.equal(isImplementedOperation('POST', '/auth/register'), false);
  assert.equal(isImplementedOperation('GET', '/classes'), true);
  assert.equal(isImplementedOperation('GET', '/classes/00000000-0000-4000-8000-000000000101/posts'), true);
  assert.equal(isImplementedOperation('PUT', '/admin/classes/00000000-0000-4000-8000-000000000101/members'), true);
  assert.equal(isImplementedOperation('GET', '/admin/students/imports/00000000-0000-4000-8000-000000000101'), true);
  assert.equal(isImplementedOperation('DELETE', '/classes/00000000-0000-4000-8000-000000000101/posts/00000000-0000-4000-8000-000000000102'), true);
  assert.equal(isImplementedOperation('GET', '/classes/not-a-uuid/posts'), false);
  assert.equal(isImplementedOperation('GET', '/internal/v1/classes/x/access'), false);
  assert.equal(isImplementedOperation('POST', '/admin/students/../../auth/login/activation-codes'), false);
});
test('proxy preserves backend cookies, origin, retry/correlation metadata without service keys', async () => {
  let forwarded: Headers | undefined;
  const proxy = createCoreProxy(() => 'http://core:8000', (async (url, init) => {
    assert.equal(String(url), 'http://core:8000/api/v1/auth/login'); forwarded = new Headers(init?.headers);
    const headers = new Headers({ 'X-Correlation-ID': 'correlation', 'Retry-After': '3' });
    headers.append('Set-Cookie', 'refresh_token=opaque; HttpOnly; Path=/api/v1/auth');
    return Response.json({ access_token: 'opaque' }, { headers });
  }) as typeof fetch);
  const response = await proxy(new Request('http://localhost:3000/api/v1/auth/login', { method: 'POST', headers: { Origin: 'http://localhost:3000', 'X-Service-Key': 'must-not-forward' }, body: '{}' }), ['auth','login']);
  assert.equal(response.status, 200); assert.equal(forwarded!.get('Origin'), 'http://localhost:3000'); assert.equal(forwarded!.has('X-Service-Key'), false);
  assert.equal(response.headers.getSetCookie().length, 1); assert.equal(response.headers.get('Cache-Control'), 'no-store'); assert.equal(response.headers.get('X-Correlation-ID'), 'correlation');
});
test('upstream outage produces 503, does not fall back to local data', async () => {
  const proxy = createCoreProxy(() => 'http://core:8000', (async () => { throw new TypeError('down'); }) as typeof fetch);
  const response = await proxy(new Request('http://localhost:3000/api/v1/users/me'), ['users','me']);
  assert.equal(response.status, 503); assert.equal((await response.json()).error.code, 'UPSTREAM_UNAVAILABLE');
});
test('missing configuration, cross-origin mutation and uncontracted paths are closed', async () => {
  let calls = 0; const fetcher = (async () => { calls++; return Response.json({}); }) as typeof fetch;
  assert.equal((await createCoreProxy(() => undefined, fetcher)(new Request('http://local/api/v1/users/me'), ['users','me'])).status, 503);
  assert.equal((await createCoreProxy(() => 'http://core', fetcher)(new Request('http://local/api/v1/auth/login', { method: 'POST', headers: { Origin: 'http://evil' } }), ['auth','login'])).status, 403);
  assert.equal((await createCoreProxy(() => 'http://core', fetcher)(new Request('http://local/api/v1/internal/v1/classes/x/access'), ['internal','v1','classes','x','access'])).status, 404); assert.equal(calls, 0);
});

test('proxy forwards only documented query parameters and Admin mutation headers', async () => {
  let target = '';
  let forwarded: Headers | undefined;
  const proxy = createCoreProxy(() => 'http://core:8000', (async (url, init) => {
    target = String(url);
    forwarded = new Headers(init?.headers);
    return Response.json({ id: 'ok' });
  }) as typeof fetch, () => 'https://school.example');
  const classId = '00000000-0000-4000-8000-000000000101';
  const response = await proxy(new Request(`https://school.example/api/v1/classes/${classId}/posts?limit=10&cursor=opaque&ignored=secret`, { headers: { Authorization: 'Bearer opaque', 'Idempotency-Key': '00000000-0000-4000-8000-000000000103', 'X-Service-Key': 'must-not-forward' } }), ['classes', classId, 'posts']);
  assert.equal(response.status, 200);
  const query = new URL(target).searchParams;
  assert.equal(new URL(target).pathname, `/api/v1/classes/${classId}/posts`);
  assert.deepEqual([...query.entries()].sort(), [['cursor', 'opaque'], ['limit', '10']]);
  const mutation = await proxy(new Request('https://school.example/api/v1/admin/users', { method: 'POST', headers: { Origin: 'https://school.example', 'X-Admin-Reason': 'ADMIN_INTERVENTION', 'Idempotency-Key': '00000000-0000-4000-8000-000000000103', 'X-Service-Key': 'must-not-forward' }, body: '{}' }), ['admin', 'users']);
  assert.equal(mutation.status, 200);
  assert.equal(forwarded?.get('Idempotency-Key'), '00000000-0000-4000-8000-000000000103');
  assert.equal(forwarded?.get('X-Admin-Reason'), 'ADMIN_INTERVENTION');
  assert.equal(forwarded?.has('X-Service-Key'), false);
});

const documentId = '00000000-0000-4000-8000-000000000201';
const documentClassId = '00000000-0000-4000-8000-000000000202';
const noteId = '00000000-0000-4000-8000-000000000203';
const pdfBytes = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x80, 0xff, 0x00, 0xfe]);
const origins = { core: () => 'http://core:8000', document: () => 'http://document:8001' };

test('proxy preserves every multipart byte and the original boundary', async () => {
  const form = new FormData();
  form.set('title', 'PDF');
  form.set('file', new Blob([pdfBytes], { type: 'application/pdf' }), 'sample.pdf');
  const request = new Request('http://web/api/v1/documents', { method: 'POST', headers: { Origin: 'http://web' }, body: form });
  const type = request.headers.get('content-type');
  assert.match(type ?? '', /^multipart\/form-data; boundary=.+/);
  const originalBody = await request.clone().arrayBuffer();
  const proxy = createCoreProxy(origins, async (target, init) => {
    assert.equal(String(target), 'http://document:8001/api/v1/documents');
    assert.equal(new Headers(init?.headers).get('content-type'), type);
    assert.deepEqual(new Uint8Array(init?.body as ArrayBuffer), new Uint8Array(originalBody));
    const posted = await new Response(init?.body, { headers: init?.headers }).formData();
    assert.deepEqual(new Uint8Array(await (posted.get('file') as File).arrayBuffer()), pdfBytes);
    return Response.json({}, { status: 201 });
  });
  assert.equal((await proxy(request, ['documents'])).status, 201);
});

test('proxy routes Document resources by operation and isolates invalid or missing Document configuration', async () => {
  const hits: string[] = [];
  const upstream: typeof fetch = async target => {
    hits.push(String(target));
    return Response.json({});
  };
  const proxy = createCoreProxy(origins, upstream);
  const send = (handler: typeof proxy, method: string, path: string) =>
    handler(new Request(`http://web/api/v1/${path}`, { method, headers: { Origin: 'http://web' } }), path.split('/'));
  const cases = [
    ['GET', 'documents/policy'], ['GET', 'documents'], ['GET', `classes/${documentClassId}/documents`],
    ['POST', `classes/${documentClassId}/documents/grants`], ['POST', `classes/${documentClassId}/notes`],
    ['PATCH', `notes/${noteId}`], ['GET', 'users/me/notes'],
    ['POST', `classes/${documentClassId}/bookmarks/toggle`], ['GET', 'users/me/bookmarks'],
  ];
  for (const [method, path] of cases) {
    assert.equal((await send(proxy, method, path)).status, 200);
    assert.equal(hits.at(-1), `http://document:8001/api/v1/${path}`);
  }
  const posts = `classes/${documentClassId}/posts`;
  assert.equal((await send(proxy, 'GET', posts)).status, 200);
  assert.equal(hits.at(-1), `http://core:8000/api/v1/${posts}`);
  for (const config of [origins.core, { core: origins.core }, { core: origins.core, document: () => 'http://document/base' }]) {
    const isolated = createCoreProxy(config, upstream);
    const count = hits.length;
    const failed = await send(isolated, 'GET', 'documents/policy');
    assert.equal(failed.status, 503);
    assert.deepEqual((await failed.json()).error, { code: 'CONFIGURATION_ERROR', message: 'Dịch vụ tài liệu chưa sẵn sàng.' });
    assert.equal(hits.length, count);
    assert.equal((await send(isolated, 'GET', posts)).status, 200);
  }
});

test('Document path templates validate every UUID and downloads preserve file headers and bytes', async () => {
  assert.throws(() => operationPath('acceptDocumentVersion', { documentId, versionId: 'bad' }), /UUID/);
  assert.equal(findOperation('GET', `/classes/${documentClassId}/documents/bad/reader`), undefined);
  assert.equal(findOperation('GET', `/classes/bad/documents/${documentId}/reader`), undefined);
  const path = `classes/${documentClassId}/documents/${documentId}/download`;
  const headers = {
    'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="sample.pdf"',
    'Content-Length': String(pdfBytes.length), 'X-Correlation-ID': 'correlation', 'Retry-After': '3',
    'X-Token-Delivery-Status': 'delivered',
  };
  const proxy = createCoreProxy(origins, async (target, init) => {
    assert.equal(String(target), `http://document:8001/api/v1/${path}?version=2`);
    assert.equal(new Headers(init?.headers).get('accept'), '*/*');
    assert.equal(new Headers(init?.headers).has('x-service-key'), false);
    assert.equal(new Headers(init?.headers).has('cookie'), false);
    assert.equal(init?.body, undefined);
    return new Response(pdfBytes, { headers: { ...headers, 'Cache-Control': 'public', 'Set-Cookie': 'untrusted=1' } });
  });
  const response = await proxy(new Request(`http://web/api/v1/${path}?version=2&extra=ignored`, {
    headers: { 'X-Service-Key': 'blocked', Cookie: 'refresh=opaque' },
  }), path.split('/'));
  assert.equal(response.status, 200);
  for (const [name, value] of Object.entries(headers)) assert.equal(response.headers.get(name), value);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.has('set-cookie'), false);
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), pdfBytes);
});

test('HTTP transport passes FormData through untouched and accepts both grant confirmations', async () => {
  const form = new FormData();
  form.set('file', new Blob([pdfBytes]), 'sample.pdf');
  const client = createHttpClient(() => 'opaque', async (target, init) => {
    assert.equal(target, '/api/v1/documents');
    assert.equal(new Headers(init?.headers).has('content-type'), false);
    assert.equal(init?.body, form);
    return Response.json({}, { status: 201 });
  });
  await client('createDocument', { body: form });
  for (const status of [200, 201]) {
    const grantClient = createHttpClient(() => 'opaque', async () => Response.json({}, { status }));
    await grantClient('grantClassDocument', { pathParams: { classId: documentClassId } });
  }
});
test('return target rejects external URLs, unknown paths and wrong roles', () => {
  assert.equal(safeReturnTo('//evil/path', ['STUDENT'], '/student'), '/student');
  assert.equal(safeReturnTo('/admin/accounts', ['STUDENT'], '/student'), '/student');
  assert.equal(safeReturnTo('/student/classes/../../admin/accounts', ['STUDENT'], '/student'), '/student');
  assert.equal(safeReturnTo('/student/classes/class-1/documents', ['STUDENT'], '/student'), '/student/classes/class-1/documents');
});


test('Webpack and Turbopack resolve the same mode-specific adapter and keep output isolation', () => {
  const previous = process.env.NEXT_PUBLIC_DATA_MODE;
  try {
    for (const mode of ['live', 'mock']) {
      process.env.NEXT_PUBLIC_DATA_MODE = mode;
      const config = configure('phase-development-server');
      const expected = mode === 'mock' ? './dev/mocks/adapter.ts' : './src/lib/data/live.ts';
      assert.equal(config.turbopack.resolveAlias['@data-adapter'], expected);
      const webpack = { resolve: { alias: {} as Record<string, string> } };
      config.webpack(webpack);
      assert.ok(webpack.resolve.alias['@data-adapter$'].replaceAll('\\', '/').endsWith(expected.slice(1)));
      assert.equal(config.distDir, mode === 'mock' ? '.next-preview' : '.next');
    }
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_DATA_MODE; else process.env.NEXT_PUBLIC_DATA_MODE = previous;
  }
});
