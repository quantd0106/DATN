import { findOperation, operationQueryParameters, operationTimeoutMs, operationUpstream } from './operations';
import { findLearningOperation } from './learning-operations';
import { aiStreamTimeoutMs, findAiOperation } from './ai-operations';

type OriginResolver = () => string | undefined;
type UpstreamResolvers = { core: OriginResolver; document?: OriginResolver };

function failure(code: string, message: string, status: number) {
  return Response.json({ error: { code, message } }, {
    status, headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' },
  });
}
export function createCoreProxy(resolver: OriginResolver | UpstreamResolvers, fetcher: typeof fetch = fetch, getWebOrigin?: OriginResolver) {
  const origins = typeof resolver === 'function' ? { core: resolver } : resolver;
  return async function proxy(request: Request, parts: string[]) {
    const path = '/' + parts.join('/');
    const operation = findOperation(request.method, path);
    if (parts.some(p => !p || p.includes('/') || p.includes('\\') || p === '.' || p === '..') || !operation) {
      return failure('NOT_FOUND', 'Không tìm thấy nội dung.', 404);
    }
    const method = request.method;
    let publicOrigin: string;
    try {
      const configured = getWebOrigin ? getWebOrigin() : new URL(request.url).origin;
      if (!configured) throw new Error();
      const origin = new URL(configured);
      if (!['http:', 'https:'].includes(origin.protocol) || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash) throw new Error();
      publicOrigin = origin.origin;
    } catch { return failure('CONFIGURATION_ERROR', 'Dịch vụ chưa sẵn sàng.', 503); }
    const hasRequestBody = method !== 'GET' && method !== 'HEAD';
    if (hasRequestBody && request.headers.get('Origin') !== publicOrigin) {
      return failure('FORBIDDEN', 'Yêu cầu không hợp lệ.', 403);
    }
    const service = operationUpstream(operation);
    let origin: URL;
    try {
      const configured = origins[service]?.(); if (!configured) throw new Error();
      origin = new URL(configured);
      if (!['http:', 'https:'].includes(origin.protocol) || origin.username || origin.password || origin.search || origin.hash || origin.pathname !== '/') throw new Error();
    } catch {
      return failure('CONFIGURATION_ERROR', service === 'document' ? 'Dịch vụ tài liệu chưa sẵn sàng.' : 'Dịch vụ chưa sẵn sàng.', 503);
    }
    const target = new URL('/api/v1' + path, origin);
    const incoming = new URL(request.url);
    // Forward only query parameters explicitly documented for the matched operation.
    for (const key of operationQueryParameters[operation] ?? []) {
      if (incoming.searchParams.has(key)) target.searchParams.set(key, incoming.searchParams.get(key)!);
    }
    const download = operation === 'classDocumentDownload' || operation === 'libraryDocumentDownload';
    const headers = new Headers({ Accept: download ? '*/*' : 'application/json' });
    if (download) headers.set('Accept-Encoding', 'identity');
    const requestHeaders = ['authorization', 'content-type', 'origin', 'referer', 'x-correlation-id'];
    if (service === 'core') requestHeaders.push('cookie', 'x-admin-reason', 'idempotency-key');
    for (const name of requestHeaders) {
      const value = request.headers.get(name); if (value) headers.set(name, value);
    }
    try {
      const body = hasRequestBody ? await request.arrayBuffer() : undefined;
      const upstream = await fetcher(target, {
        method, headers, body: body?.byteLength ? body : undefined, cache: 'no-store', redirect: 'manual',
        signal: AbortSignal.timeout(operationTimeoutMs(operation)),
      });
      if (upstream.status >= 300 && upstream.status < 400) return failure('UPSTREAM_ERROR', 'Dịch vụ chưa sẵn sàng.', 502);
      const responseHeaders = new Headers({ 'Cache-Control': 'no-store' });
      const allowedHeaders = ['content-type', 'x-correlation-id', 'retry-after', 'x-token-delivery-status'];
      if (download) {
        allowedHeaders.push('content-disposition');
        const encoding = upstream.headers.get('content-encoding');
        if (!encoding || encoding === 'identity') allowedHeaders.push('content-length');
      }
      for (const name of allowedHeaders) { const value = upstream.headers.get(name); if (value) responseHeaders.set(name, value); }
      if (service === 'core') {
        for (const cookie of upstream.headers.getSetCookie()) responseHeaders.append('Set-Cookie', cookie);
      }
      const responseBody = upstream.status === 204 ? null : download && upstream.ok ? upstream.body : await upstream.arrayBuffer();
      return new Response(responseBody, { status: upstream.status, headers: responseHeaders });
    } catch { return failure('UPSTREAM_UNAVAILABLE', 'Chưa kết nối được máy chủ. Vui lòng thử lại sau.', 503); }
  };
}

function serviceOrigin(configured: string | undefined): URL {
  if (!configured) throw new Error('Missing origin');
  const url = new URL(configured);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      url.pathname !== '/' || url.search || url.hash) throw new Error('Invalid origin');
  return url;
}

export function createLearningProxy(
  getOrigin: () => string | undefined,
  fetcher: typeof fetch = fetch,
  getWebOrigin?: () => string | undefined,
) {
  return async function proxy(request: Request, parts: string[]) {
    const operation = findLearningOperation(request.method, parts);
    if (!operation) return failure('NOT_FOUND', 'Không tìm thấy nội dung.', 404);

    if (operation.method !== 'GET') {
      let webOrigin: string;
      try { webOrigin = serviceOrigin(getWebOrigin ? getWebOrigin() : new URL(request.url).origin).origin; }
      catch { return failure('CONFIGURATION_ERROR', 'Dịch vụ học tập chưa sẵn sàng.', 503); }
      if (request.headers.get('Origin') !== webOrigin) {
        return failure('FORBIDDEN', 'Yêu cầu không hợp lệ.', 403);
      }
    }

    let target: URL;
    try {
      target = new URL('/api/v1/' + parts.map(encodeURIComponent).join('/'), serviceOrigin(getOrigin()));
    } catch {
      return failure('CONFIGURATION_ERROR', 'Dịch vụ học tập chưa sẵn sàng.', 503);
    }

    const incoming = new URL(request.url);
    for (const key of operation.query) {
      const value = incoming.searchParams.get(key);
      if (value !== null) target.searchParams.set(key, value);
    }

    const headers = new Headers({ Accept: operation.mode === 'binary' ? 'application/octet-stream' : 'application/json' });
    for (const name of ['authorization', 'content-type', 'x-correlation-id', 'idempotency-key', 'x-admin-reason', 'accept']) {
      const value = request.headers.get(name);
      if (value) headers.set(name, value);
    }

    try {
      const body = operation.method === 'GET' ? undefined : await request.arrayBuffer();
      const upstream = await fetcher(target, {
        method: operation.method, headers, body, cache: 'no-store', redirect: 'manual',
        signal: AbortSignal.any([request.signal, AbortSignal.timeout(15000)]),
      });
      if (upstream.status >= 300 && upstream.status < 400) {
        await upstream.body?.cancel();
        return failure('UPSTREAM_ERROR', 'Phản hồi dịch vụ học tập không hợp lệ.', 502);
      }
      const responseHeaders = new Headers({
        'Cache-Control': 'no-store',
        'Content-Security-Policy': "default-src 'none'; sandbox",
        'X-Content-Type-Options': 'nosniff',
      });
      const forwarded = ['content-type', 'x-correlation-id', 'retry-after'];
      if (operation.mode === 'binary') forwarded.push('content-disposition', 'x-content-type-options');
      for (const name of forwarded) {
        const value = upstream.headers.get(name);
        if (value) responseHeaders.set(name, value);
      }
      return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
    } catch {
      return failure('UPSTREAM_UNAVAILABLE', 'Dịch vụ học tập đang tạm thời gián đoạn.', 503);
    }
  };
}

export function createAiProxy(
  getOrigin: () => string | undefined,
  fetcher: typeof fetch = fetch,
  getWebOrigin: () => string | undefined = () => undefined,
) {
  return async function proxy(request: Request, parts: string[]) {
    const match = findAiOperation(request.method, parts);
    if (!match) return failure('NOT_FOUND', 'Không tìm thấy nội dung.', 404);
    const [id, operation] = match;
    const { method, mode } = operation;

    let target: URL;
    try {
      const configured = getOrigin();
      if (!configured || !/^https?:\/\/[^/?#\\\s@]+\/?$/i.test(configured)) throw new Error('Invalid AI origin');
      target = new URL('/api/v1/' + parts.map(encodeURIComponent).join('/'), serviceOrigin(configured));
      if (method !== 'GET' && request.headers.get('Origin') !== serviceOrigin(getWebOrigin()).origin) {
        return failure('FORBIDDEN', 'Yêu cầu không hợp lệ.', 403);
      }
    } catch {
      return failure('CONFIGURATION_ERROR', 'Dịch vụ AI chưa sẵn sàng.', 503);
    }

    const incoming = new URL(request.url);
    for (const key of operation.query) {
      const value = incoming.searchParams.get(key);
      if (value !== null) target.searchParams.set(key, value);
    }
    const headers = new Headers({ Accept: 'application/json' });
    for (const name of ['authorization', 'content-type', 'x-correlation-id', 'accept']) {
      const value = request.headers.get(name);
      if (value) headers.set(name, value);
    }
    if (mode === 'sse') headers.set('Accept', 'text/event-stream');

    try {
      const timeout = mode === 'sse' ? aiStreamTimeoutMs : id === 'proposeQuiz' ? 30_000 : 15_000;
      const upstream = await fetcher(target, {
        method, headers, body: method === 'GET' ? undefined : await request.arrayBuffer(),
        cache: 'no-store', redirect: 'manual',
        signal: AbortSignal.any([request.signal, AbortSignal.timeout(timeout)]),
      });
      if (upstream.status >= 300 && upstream.status < 400) {
        void upstream.body?.cancel().catch(() => undefined);
        return failure('UPSTREAM_ERROR', 'Phản hồi dịch vụ AI không hợp lệ.', 502);
      }
      const responseHeaders = new Headers({
        'Cache-Control': 'no-store',
        'Content-Security-Policy': "default-src 'none'; sandbox",
        'X-Content-Type-Options': 'nosniff',
      });
      for (const name of ['content-type', 'x-correlation-id', 'retry-after']) {
        const value = upstream.headers.get(name);
        if (value) responseHeaders.set(name, value);
      }
      if (mode === 'sse' && upstream.ok) responseHeaders.set('X-Accel-Buffering', 'no');
      return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
    } catch {
      return failure('UPSTREAM_UNAVAILABLE', 'Dịch vụ AI đang tạm thời gián đoạn.', 503);
    }
  };
}
