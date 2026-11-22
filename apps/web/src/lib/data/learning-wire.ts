import { decodeMutation } from '../api/decoders';
import { ApiError } from '../api/errors';
import type { LearningHttpRequest } from '../api/http';
import { learningOperations, learningUuid } from '../api/learning-operations';
import type { LiveIdentitySession } from './live-identity';
import type { createLiveCore } from './live-core';

type LiveCore = ReturnType<typeof createLiveCore>;
export type LearningCore = Pick<LiveCore, 'readClass' | 'readClasses' | 'readEvents'> &
  Partial<Pick<LiveCore, 'readClassMembers'>>;
export const unavailableMessage = 'Dịch vụ học tập đang tạm thời gián đoạn. Vui lòng thử lại sau.';
export function unavailable(message = unavailableMessage): never {
  throw new ApiError('UNAVAILABLE', message, 503);
}

export function invalid(): never {
  throw new ApiError('CONTRACT_MISMATCH', 'Dữ liệu dịch vụ học tập không hợp lệ.', 502);
}
export function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}
export function text(value: unknown): string {
  return typeof value === 'string' ? value : invalid();
}
export function uuid(value: unknown): string {
  const id = text(value);
  return learningUuid.test(id) ? id : invalid();
}
export function number(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : invalid();
}
export function integer(value: unknown, minimum = 0): number {
  const count = number(value);
  return Number.isSafeInteger(count) && count >= minimum ? count : invalid();
}
export function boolean(value: unknown): boolean {
  return typeof value === 'boolean' ? value : invalid();
}
export function timestamp(value: unknown): string {
  const date = text(value);
  return /(?:Z|[+-]\d\d:\d\d)$/i.test(date) && Number.isFinite(Date.parse(date)) ? date : invalid();
}
export function enumeration<const T extends string>(value: unknown, ...choices: T[]): T {
  return choices.find(choice => choice === value) ?? invalid();
}
export function optional<T>(value: unknown, decode: (value: unknown) => T): T | undefined {
  return value == null ? undefined : decode(value);
}
export function nullable<T>(value: unknown, decode: (value: unknown) => T): T | null {
  return value === null ? null : decode(value);
}
export function list<T>(value: unknown, decode: (value: unknown) => T): T[] {
  return Array.isArray(value) ? value.map(item => decode(item)) : invalid();
}
export function dictionary<T>(value: unknown, decode: (value: unknown, key: string) => T): Record<string, T> {
  return Object.fromEntries(Object.entries(record(value)).map(([key, item]) => [key, decode(item, key)]));
}
export function questionType(value: unknown) {
  return enumeration(value, 'single_choice', 'multiple_choice', 'true_false', 'short_answer');
}

export function learningReader(session: LiveIdentitySession) {
  return async function read<T>(
    id: Parameters<LearningHttpRequest>[0], options: Parameters<LearningHttpRequest>[1], decode: (value: unknown) => T,
  ): Promise<T> {
    const mutation = learningOperations[id].method !== 'GET';
    return session.authenticatedLearning(async request => {
      const value = await request(id, options);
      return mutation ? decodeMutation(decode, value) : decode(value);
    }, mutation);
  };
}

export function requireLearningSession(session: LiveIdentitySession, expected: number) {
  if (session.sessionGeneration() !== expected) {
    throw new ApiError('SESSION_CHANGED', 'Phiên đăng nhập đã thay đổi. Hãy tải lại nội dung.', 401);
  }
}

export async function optionalServiceRead<T>(
  run: () => Promise<T>,
  fallback: T,
  signal?: AbortSignal,
): Promise<T> {
  try {
    return await run();
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
    if (error instanceof ApiError && error.status === 401) throw error;
    return fallback;
  }
}

export async function readHistoryClass(
  core: Pick<LearningCore, 'readClass'>,
  classId: string,
  signal?: AbortSignal,
) {
  try { return await core.readClass(classId, signal); }
  catch (error) {
    if (error instanceof ApiError && [403, 404].includes(error.status)) return undefined;
    throw error;
  }
}
