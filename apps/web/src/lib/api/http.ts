import { ApiError } from './errors';
import { aiOperationPath, aiOperations, aiStreamTimeoutMs, type AiOperationId } from './ai-operations';
import { learningOperationPath, learningOperations, type LearningOperationId } from './learning-operations';
import { isExpectedSuccessStatus, operationPath, operationQueryParameters, operations, operationTimeoutMs, type OperationId } from './operations';

export type HttpOptions = { body?: unknown; userId?: string; pathParams?: Record<string, string>; cursor?: string; query?: Record<string, string | number | undefined>; headers?: Record<string, string>; onResponse?: (response: Response) => void; signal?: AbortSignal; timeoutMs?: number };
export type HttpRequest = (id: OperationId, options?: HttpOptions) => Promise<unknown>;
export function createHttpClient(getToken: () => string | null, fetcher: typeof fetch = fetch) {
  function request(id: 'classDocumentDownload', options?: HttpOptions): Promise<Blob>;
  function request(id: OperationId, options?: HttpOptions): Promise<unknown>;
  async function request(id: OperationId, options: HttpOptions = {}): Promise<unknown> {
    const [method] = operations[id];
    const download = id === 'classDocumentDownload';
    const form = options.body instanceof FormData ? options.body : undefined;
    const controller = new AbortController();
    const abort = () => controller.abort();
    options.signal?.addEventListener('abort', abort, { once: true });
    if (options.signal?.aborted) controller.abort();
    const timer = setTimeout(abort, options.timeoutMs ?? operationTimeoutMs(id));
    const headers = new Headers({ Accept: download ? '*/*' : 'application/json' });
    const token = getToken();
    if (token) headers.set('Authorization', `Bearer ${token}`);
    if (options.body !== undefined && !form) headers.set('Content-Type', 'application/json');
    for (const [name, value] of Object.entries(options.headers ?? {})) {
      if (['x-admin-reason', 'idempotency-key'].includes(name.toLowerCase())) headers.set(name, value);
    }
    // No generic Idempotency-Key or retry: provisioning/activation forbid it,
    // and unknown mutations must be reconciled by the feature-specific contract.
    try {
      const pathParams = { ...(options.pathParams ?? {}), ...(options.userId ? { userId: options.userId } : {}) };
      const path = operationPath(id, pathParams);
      const query = new URLSearchParams();
      const values: Record<string, string | number | undefined> = { ...(options.query ?? {}), ...(options.cursor ? { cursor: options.cursor } : {}) };
      for (const key of operationQueryParameters[id] ?? []) {
        const value = values[key];
        if (value !== undefined) query.set(key, String(value));
      }
      const queryString = query.toString();
      const suffix = queryString ? `?${queryString}` : '';
      const response = await fetcher(`/api/v1${path}${suffix}`, {
        method, headers, credentials: 'include', cache: 'no-store', signal: controller.signal,
        body: form ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
      });
      options.onResponse?.(response);
      if (response.ok && !isExpectedSuccessStatus(id, response.status)) throw new ApiError('CONTRACT_MISMATCH', 'Phản hồi chưa đúng trạng thái xác nhận.', response.status, method === 'GET' ? 'rejected' : 'unknown');
      if (response.status === 204) return undefined;
      if (download && response.ok) return await response.blob();
      let json: unknown;
      try { json = await response.json(); } catch {
        throw new ApiError('INVALID_RESPONSE', 'Phản hồi chưa hợp lệ. Vui lòng thử lại sau.', response.status, method === 'GET' ? 'rejected' : 'unknown');
      }
      if (!response.ok) {
        const detail = typeof json === 'object' && json !== null && 'error' in json ? (json as { error: { code?: string; message?: string } }).error : null;
        throw new ApiError(detail?.code ?? 'REQUEST_FAILED', typeof detail?.message === 'string' ? detail.message : 'Chưa thể hoàn tất yêu cầu.', response.status,
          method !== 'GET' && response.status >= 500 ? 'unknown' : 'rejected', response.headers.get('X-Correlation-ID') ?? undefined, response.headers.get('Retry-After') ?? undefined);
      }
      return json;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError('CONNECTION_ERROR', 'Chưa kết nối được máy chủ. Vui lòng kiểm tra kết nối.', 0, method === 'GET' ? 'rejected' : 'unknown');
    } finally {
      clearTimeout(timer); options.signal?.removeEventListener('abort', abort);
    }
  }
  return request;
}

type LearningHttpOptions = Pick<HttpOptions, 'body' | 'query' | 'headers' | 'onResponse' | 'signal'> & {
  pathParams?: Record<string, unknown>;
};
export type LearningHttpRequest = (id: LearningOperationId, options?: LearningHttpOptions) => Promise<unknown>;

export function createLearningHttpClient(getToken: () => string | null, fetcher: typeof fetch = fetch): LearningHttpRequest {
  return async (id, options = {}) => {
    const { method, statuses, query, mode } = learningOperations[id];
    const mutation = method !== 'GET';
    let path: string;
    try { path = learningOperationPath(id, options.pathParams); }
    catch { throw new ApiError('BAD_REQUEST', 'Đường dẫn học tập không hợp lệ.', 400); }

    const params = new URLSearchParams();
    for (const key of query) {
      const value = options.query?.[key];
      if (value !== undefined) params.set(key, String(value));
    }
    const suffix = params.size ? `?${params}` : '';
    const headers = new Headers({ Accept: mode === 'binary' ? 'application/octet-stream' : 'application/json' });
    const token = getToken();
    if (token) headers.set('Authorization', `Bearer ${token}`);
    for (const [name, value] of Object.entries(options.headers ?? {})) {
      if (['x-admin-reason', 'idempotency-key', 'x-correlation-id'].includes(name.toLowerCase())) headers.set(name, value);
    }
    const timeout = AbortSignal.timeout(15000);
    const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
    try {
      let body: BodyInit | undefined;
      if (options.body instanceof FormData) body = options.body;
      else if (options.body !== undefined) {
        body = JSON.stringify(options.body);
        headers.set('Content-Type', 'application/json');
      }
      const response = await fetcher(`/api/learning/v1${path}${suffix}`, {
        method, headers, body, credentials: 'same-origin', cache: 'no-store', signal,
      });
      const outcome = mutation ? 'unknown' : 'rejected';
      if (!response.ok) {
        const json = await response.json().catch(() => null);
        const detail = json?.error;
        throw new ApiError(typeof detail?.code === 'string' ? detail.code : 'REQUEST_FAILED',
          typeof detail?.message === 'string' ? detail.message : 'Chưa thể hoàn tất yêu cầu học tập.',
          response.status, response.status >= 500 ? outcome : 'rejected',
          response.headers.get('X-Correlation-ID') ?? undefined, response.headers.get('Retry-After') ?? undefined);
      }
      if (!statuses.includes(response.status)) {
        throw new ApiError('CONTRACT_MISMATCH', 'Phản hồi chưa đúng trạng thái xác nhận.', response.status, outcome);
      }
      options.onResponse?.(response);
      if (mode === 'empty') return undefined;
      try {
        if (mode === 'binary') {
          if (response.headers.get('Content-Type')?.split(';')[0]?.trim().toLowerCase() !== 'application/octet-stream') throw new Error();
          return new Uint8Array(await response.arrayBuffer());
        }
        return await response.json();
      } catch {
        throw new ApiError('INVALID_RESPONSE', 'Phản hồi dịch vụ học tập không hợp lệ.', response.status, outcome);
      }
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError('CONNECTION_ERROR', 'Chưa kết nối được dịch vụ học tập.', 0, mutation ? 'unknown' : 'rejected');
    }
  };
}

export type AiStreamEvent = { event: string; data: unknown };
type AiHttpOptions = LearningHttpOptions & { onEvent?: (event: AiStreamEvent) => void };
export type AiHttpRequest = (id: AiOperationId, options?: AiHttpOptions) => Promise<unknown>;

async function readAiEvents(body: ReadableStream<Uint8Array>, signal: AbortSignal, onEvent?: AiHttpOptions['onEvent']) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const events: AiStreamEvent[] = [];
  const abort = () => { void reader.cancel().catch(() => undefined); };
  signal.addEventListener('abort', abort, { once: true });
  let buffer = '';
  try {
    signal.throwIfAborted();
    while (true) {
      const { done, value } = await reader.read();
      signal.throwIfAborted();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let separator: RegExpExecArray | null;
      while ((separator = /\r?\n\r?\n/.exec(buffer))) {
        const lines = buffer.slice(0, separator.index).split(/\r?\n/);
        buffer = buffer.slice(separator.index + separator[0].length);
        let event = 'message';
        const dataLines: string[] = [];
        for (const line of lines) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
        }
        if (!dataLines.length) continue;
        let data: unknown;
        try { data = JSON.parse(dataLines.join('\n')); }
        catch { throw new ApiError('INVALID_RESPONSE', 'Sự kiện từ dịch vụ AI không hợp lệ.', 502, 'unknown'); }
        const entry = { event, data };
        events.push(entry);
        onEvent?.(entry);
        signal.throwIfAborted();
      }
      if (done) return events;
    }
  } finally {
    signal.removeEventListener('abort', abort);
    abort();
    reader.releaseLock();
  }
}

export function createAiHttpClient(getToken: () => string | null, fetcher: typeof fetch = fetch): AiHttpRequest {
  return async (id, options = {}) => {
    const { method, status, query, mode } = aiOperations[id];
    const outcome = method === 'GET' ? 'rejected' : 'unknown';
    let path: string;
    try { path = aiOperationPath(id, options.pathParams); }
    catch { throw new ApiError('BAD_REQUEST', 'Đường dẫn dịch vụ AI không hợp lệ.', 400); }

    const params = new URLSearchParams();
    for (const key of query) {
      const value = options.query?.[key];
      if (value !== undefined) params.set(key, String(value));
    }
    const headers = new Headers({ Accept: mode === 'sse' ? 'text/event-stream' : 'application/json' });
    const token = getToken();
    if (token) headers.set('Authorization', `Bearer ${token}`);
    if (options.body !== undefined) headers.set('Content-Type', 'application/json');
    for (const [name, value] of Object.entries(options.headers ?? {})) {
      if (name.toLowerCase() === 'x-correlation-id') headers.set(name, value);
    }
    const timeout = AbortSignal.timeout(mode === 'sse' ? aiStreamTimeoutMs : id === 'proposeQuiz' ? 30_000 : 15_000);
    const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
    try {
      signal.throwIfAborted();
      const response = await fetcher(`/api/ai/v1${path}${params.size ? `?${params}` : ''}`, {
        method, headers, body: options.body === undefined ? undefined : JSON.stringify(options.body),
        credentials: 'same-origin', cache: 'no-store', signal,
      });
      signal.throwIfAborted();
      if (!response.ok) {
        const detail = (await response.json().catch(() => null))?.error;
        signal.throwIfAborted();
        throw new ApiError(typeof detail?.code === 'string' ? detail.code : 'REQUEST_FAILED',
          typeof detail?.message === 'string' ? detail.message : 'Chưa thể hoàn tất yêu cầu AI.',
          response.status, response.status >= 500 ? outcome : 'rejected',
          response.headers.get('X-Correlation-ID') ?? undefined, response.headers.get('Retry-After') ?? undefined);
      }
      if (response.status !== status) {
        throw new ApiError('CONTRACT_MISMATCH', 'Phản hồi dịch vụ AI chưa đúng trạng thái xác nhận.', response.status, outcome);
      }
      options.onResponse?.(response);
      if (mode === 'sse') {
        if (response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'text/event-stream' || !response.body) {
          void response.body?.cancel().catch(() => undefined);
          throw new ApiError('CONTRACT_MISMATCH', 'Dịch vụ AI chưa trả luồng sự kiện hợp lệ.', 502, outcome);
        }
        return await readAiEvents(response.body, signal, options.onEvent);
      }
      try { return await response.json(); }
      catch {
        signal.throwIfAborted();
        throw new ApiError('INVALID_RESPONSE', 'Phản hồi dịch vụ AI không hợp lệ.', 502, outcome);
      }
    } catch (error) {
      if (options.signal?.aborted) throw options.signal.reason;
      if (timeout.aborted) throw new ApiError('UPSTREAM_TIMEOUT', 'Dịch vụ AI phản hồi quá thời gian chờ.', 504, outcome);
      if (error instanceof ApiError) throw error;
      throw new ApiError('CONNECTION_ERROR', 'Chưa kết nối được dịch vụ AI.', 0, outcome);
    }
  };
}
