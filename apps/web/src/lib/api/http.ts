import { ApiError } from './errors';
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
