import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import test from 'node:test';
import { aiOperations, aiOperationPath, type AiOperationId } from '../src/lib/api/ai-operations';
import { ApiError } from '../src/lib/api/errors';
import { createAiHttpClient } from '../src/lib/api/http';
import { createAiProxy } from '../src/lib/api/proxy';
import { createLiveAi } from '../src/lib/data/live-ai';
import { createLiveAssessment } from '../src/lib/data/live-assessment';
import { createLiveCore } from '../src/lib/data/live-core';
import { createLiveIdentitySession } from '../src/lib/data/live-identity';
import { createLivePracticeAuthoring } from '../src/lib/data/live-practice-authoring';

const classId = randomUUID(), conversationId = randomUUID(), otherConversationId = randomUUID();
const messageId = randomUUID(), jobId = randomUUID(), draftId = randomUUID(), documentId = randomUUID();
const documentVersionId = randomUUID(), assessmentId = randomUUID(), practiceId = randomUUID(), attemptId = randomUUID();
const userId = randomUUID(), schoolId = randomUUID();
const pathParams = { classId, conversationId, messageId, jobId, draftId };
const now = new Date().toISOString();
const classPath = `/classes/${classId}`;
const configPath = classPath + '/tutor/config';
const conversationsPath = classPath + '/tutor/conversations';
const messagesPath = `/tutor/conversations/${conversationId}/messages`;
const stopPath = messagesPath + `/${messageId}/stop`;
const quizPath = classPath + '/quiz';
const web = 'https://web.example';
const upstream = 'https://ai.example';
const sourceId = 'source-content-hash';
const errorResponse = (status: number, code: string) => Response.json({ error: { code, message: 'Dịch vụ chưa thể hoàn tất yêu cầu.' } }, { status });
const event = (name: string, data: unknown, line = '\n') => `event: ${name}${line}data: ${JSON.stringify(data)}${line}${line}`;
const start = event('start', { message_id: messageId }, '\r\n');
const streamHeaders = { 'Content-Type': 'text/event-stream; charset=utf-8' };
const parts = (path: string) => path.slice(1).split('/');

test('AI proxy forwards only the fifteen contracted operations, query keys and headers', async () => {
  const calls: { url: URL; init?: RequestInit }[] = [];
  let responseStatus = 200;
  let responseType = 'application/json';
  const proxy = createAiProxy(() => upstream, async (input, init) => {
    calls.push({ url: new URL(String(input)), init });
    return new Response('{}', {
      status: responseStatus, headers: { 'Content-Type': responseType, 'Set-Cookie': 'ignored', 'X-Correlation-ID': messageId },
    });
  }, () => web);

  for (const [id, operation] of Object.entries(aiOperations)) {
    const path = aiOperationPath(id as AiOperationId, pathParams);
    const url = new URL(`/api/ai/v1${path}?limit=8&private=true`, web);
    responseStatus = operation.status;
    responseType = operation.mode === 'sse' ? 'text/event-stream' : 'application/json';
    const headers = {
      Authorization: 'Bearer test-token', Origin: web, 'Content-Type': 'application/json',
      Accept: 'application/json', 'X-Correlation-ID': messageId, Cookie: 'refresh=private', 'X-Service-Key': 'secret',
    };
    const request = new Request(url, { method: operation.method, headers, body: operation.method === 'GET' ? undefined : '{"test":true}' });
    const response = await proxy(request, parts(path));
    assert.equal(response.status, operation.status);
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
    assert.equal(response.headers.get('X-Content-Type-Options'), 'nosniff');
    assert.equal(response.headers.get('Set-Cookie'), null);
    assert.equal(response.headers.get('X-Correlation-ID'), messageId);
    assert.equal(response.headers.get('X-Accel-Buffering'), operation.mode === 'sse' ? 'no' : null);
    const call = calls.at(-1)!;
    assert.equal(call.url.href, upstream + '/api/v1' + path + (operation.query.includes('limit') ? '?limit=8' : ''));
    assert.equal(call.init?.redirect, 'manual');
    assert.equal(call.init?.method, operation.method);
    if (operation.method !== 'GET') assert.equal(new TextDecoder().decode(call.init?.body as ArrayBuffer), '{"test":true}');
    const forwarded = new Headers(call.init?.headers);
    assert.equal(forwarded.get('Authorization'), headers.Authorization);
    assert.equal(forwarded.get('X-Correlation-ID'), messageId);
    assert.equal(forwarded.get('Content-Type'), 'application/json');
    assert.equal(forwarded.get('Accept'), operation.mode === 'sse' ? 'text/event-stream' : 'application/json');
    for (const name of ['Origin', 'Cookie', 'X-Service-Key']) assert.equal(forwarded.get(name), null);
    assert.equal(await response.text(), '{}');
  }
  assert.equal(calls.length, 15);
});

test('AI proxy rejects unlisted routes, traversal, invalid UUIDs, CSRF and broken upstreams', async () => {
  let sent = 0;
  const fetcher: typeof fetch = async () => {
    sent++;
    return new Response(null, { status: 307, headers: { Location: 'https://elsewhere.example' } });
  };
  const proxy = createAiProxy(() => upstream, fetcher, () => web);
  const req = (method = 'GET', origin?: string) => new Request(web, { method, headers: origin ? { Origin: origin } : {} });
  for (const path of ['/health/ready', '/internal/v1/explain-mistake', '/classes/../tutor/config',
    '/classes/%2e%2e/tutor/config', '/classes/%2F/tutor/config', '/classes/not-a-uuid/tutor/config',
    `/classes/${classId}\n/tutor/config`, '/classes/\\/tutor/config', configPath + '/']) {
    assert.equal((await proxy(req(), parts(path))).status, 404);
  }
  assert.equal((await proxy(req('DELETE', web), parts(configPath))).status, 404);
  for (const origin of [undefined, 'https://cross.example']) {
    assert.equal((await proxy(req('PUT', origin), parts(configPath))).status, 403);
    assert.equal((await proxy(req('POST', origin), parts(messagesPath))).status, 403);
  }
  for (const value of [undefined, 'ftp://ai.example', 'https://user@ai.example', upstream + '/api/v1', upstream + '?untrusted=1', upstream + '/path/..']) {
    const response = await createAiProxy(() => value, fetcher)(req(), parts(configPath));
    assert.equal(response.status, 503);
    assert.equal((await response.json()).error.code, 'CONFIGURATION_ERROR');
  }
  assert.equal(sent, 0);
  const redirect = await proxy(req(), parts(configPath));
  assert.equal(redirect.status, 502);
  assert.equal(redirect.headers.get('Location'), null);
  const outage = await createAiProxy(() => upstream, async () => { throw new Error('offline'); })(req(), parts(configPath));
  assert.equal(outage.status, 503);
  assert.equal((await outage.json()).error.code, 'UPSTREAM_UNAVAILABLE');
});

test('AI proxy pipes SSE before the upstream finishes and preserves JSON errors', async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(value) { controller = value; } });
  const proxy = createAiProxy(() => upstream, async () => new Response(body, { headers: streamHeaders }), () => web);
  const request = new Request(web, { method: 'POST', headers: { Origin: web } });
  const response = await proxy(request, parts(messagesPath));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Content-Type'), streamHeaders['Content-Type']);
  assert.equal(response.headers.get('X-Accel-Buffering'), 'no');
  controller.enqueue(new TextEncoder().encode(start));
  const reader = response.body!.getReader();
  assert.equal(new TextDecoder().decode((await reader.read()).value), start);
  controller.close();
  assert.equal((await reader.read()).done, true);

  const denied = createAiProxy(() => upstream, async () => errorResponse(403, 'ASSESSMENT_LOCKED'), () => web);
  const rejected = await denied(new Request(web, { method: 'POST', headers: { Origin: web } }), parts(messagesPath));
  assert.equal(rejected.status, 403);
  assert.equal(rejected.headers.get('X-Accel-Buffering'), null);
  assert.equal((await rejected.json()).error.code, 'ASSESSMENT_LOCKED');
});

type Reply = (request: Request) => Response | Promise<Response>;
function harness(replies: Record<string, Reply>, roles = ['STUDENT']) {
  const calls: string[] = [];
  let lost = 0;
  const fetcher: typeof fetch = async (input, init) => {
    const request = new Request(new URL(String(input), web), init);
    const { pathname } = new URL(request.url);
    const key = `${request.method} ${pathname}`;
    calls.push(key);
    if (['/api/v1/auth/refresh', '/api/v1/auth/login'].includes(pathname)) {
      return Response.json({ access_token: 'test-token', token_type: 'bearer', expires_in: 60 });
    }
    assert.equal(request.headers.get('Authorization'), 'Bearer test-token');
    if (pathname === '/api/v1/auth/logout') return new Response(null, { status: 204 });
    if (pathname === '/api/v1/users/me') return Response.json({
      id: userId, school_id: schoolId, roles, status: 'ACTIVE', display_name: 'Lumi', username: 'learner', email: null,
    });
    if (pathname === '/api/v1' + classPath) return Response.json({
      id: classId, name: 'Toán', grade_level: 9, status: 'ACTIVE', enrollment_mode: 'AUTO', membership_status: 'ACTIVE',
      teaching_role: roles.includes('LECTURER') ? 'PRIMARY' : null,
    });
    return replies[key]?.(request) ?? errorResponse(404, 'NOT_FOUND');
  };
  const session = createLiveIdentitySession({ fetcher, onLost: () => { lost++; } });
  const core = createLiveCore(session);
  const ai = createLiveAi(session, core, { pollDelayMs: 0 });
  return {
    calls, session, ai, lost: () => lost,
    practice: createLivePracticeAuthoring(session, core, ai),
    assessment: createLiveAssessment(session, core, ai),
  };
}
const aiKey = (method: string, path: string) => method + ' /api/ai/v1' + path;
const learningKey = (method: string, path: string) => method + ' /api/learning/v1' + path;
const listKey = aiKey('GET', conversationsPath);
const historyKey = aiKey('GET', messagesPath);
const sendKey = aiKey('POST', messagesPath);
const lockKey = learningKey('GET', classPath + '/assessments');
const conversation = (id = conversationId) => ({ id, class_id: classId, title: 'Ôn tập', default_mode: 'STEP_BY_STEP', updated_at: now });
const citation = { source_id: sourceId, quote: 'Công thức trong tài liệu', location: { document_name: 'Tài liệu Toán', page: 7 } };
const assistant = {
  id: messageId, role: 'ASSISTANT', status: 'COMPLETED', mode: 'STEP_BY_STEP', content: null, created_at: now,
  rendered: { step: { step: 2, total_steps: 4 }, claims: [
    { text: 'Bắt đầu bằng công thức', basis: 'source', citations: [citation] },
    { text: 'Thay số', basis: 'source', citations: [citation, { source_id: 'revoked', masked: true, location: null, quote: 'Hidden' }] },
  ] },
};

test('student workspace loads owned history, masks revoked citations, selects only listed conversations and shows D6', async () => {
  const unknownId = randomUUID();
  const h = harness({
    [listKey]: () => Response.json([conversation(), conversation(otherConversationId)]),
    [historyKey]: () => Response.json([
      { id: randomUUID(), role: 'USER', content: 'Giúp em giải bài này', status: 'COMPLETED', created_at: now }, assistant,
    ]),
    [aiKey('GET', `/tutor/conversations/${otherConversationId}/messages`)]: () => Response.json([]),
    [lockKey]: () => Response.json({ items: [{ id: assessmentId, active_attempt_id: attemptId, title: 'Bài kiểm tra' }] }),
  });
  const result = await h.ai.workspace.load('s.tutor', { classId, conversationId: unknownId });
  assert.equal(result.kind, 'ready');
  if (result.kind !== 'ready') return;
  const tutor = result.tutorWorkspace!;
  assert.equal(tutor.activeConversationId, conversationId);
  assert.equal(tutor.classHasSources, true);
  assert.equal(tutor.isOfficialAssessmentActive, true);
  assert.equal(tutor.assessmentTitle, 'Bài kiểm tra');
  assert.equal(tutor.assessmentHref, `/student/classes/${classId}/assessments/${assessmentId}/attempts/${attemptId}`);
  const [user, response] = tutor.conversations[0].messages;
  assert.equal(user.role, 'user');
  assert.equal(user.content, 'Giúp em giải bài này');
  assert.equal(user.timestamp, now);
  assert.equal(response.content, 'Bắt đầu bằng công thức\n\nThay số');
  assert.equal(response.knowledgeType, 'CLASS_SOURCE');
  assert.equal(response.stepNumber, 2);
  assert.equal(response.totalSteps, 4);
  assert.deepEqual(tutor.citations.map(({ isAccessible, locator, excerpt, readerHref }) => ({ isAccessible, locator, excerpt, readerHref })), [
    { isAccessible: true, locator: 'Trang 7', excerpt: citation.quote, readerHref: `/student/classes/${classId}/documents` },
    { isAccessible: false, locator: '', excerpt: '', readerHref: '#' },
  ]);
  const selected = await h.ai.workspace.load('s.tutor', { classId, conversationId: otherConversationId });
  assert.equal(selected.kind === 'ready' && selected.tutorWorkspace?.activeConversationId, otherConversationId);
  const fresh = await h.ai.workspace.load('s.tutor', { classId, compose: 'new' });
  assert.equal(fresh.kind === 'ready' && fresh.tutorWorkspace?.activeConversationId, null);
  assert.equal(h.calls.filter(call => call.includes('/messages')).length, 2);
  assert.ok(h.calls.every(call => !call.includes(unknownId)));
});

test('Tutor history tolerates unavailable Learning and exposes unavailable AI', async () => {
  for (const status of [403, 404, 503]) {
    const h = harness({ [listKey]: () => Response.json([]), [lockKey]: () => errorResponse(status, 'UNAVAILABLE') });
    const result = await h.ai.workspace.load('s.tutor', { classId });
    assert.equal(result.kind === 'ready' && result.tutorWorkspace?.isOfficialAssessmentActive, false);
  }
  const h = harness({ [listKey]: () => errorResponse(503, 'UPSTREAM_UNAVAILABLE') });
  const result = await h.ai.workspace.load('s.tutor', { classId });
  assert.equal(result.kind, 'unavailable');
});

test('Tutor creates conversations and decodes split SSE events and terminal outcomes', async () => {
  let status = 'COMPLETED';
  let operation = 'ASK';
  const h = harness({
    [aiKey('POST', conversationsPath)]: async request => {
      assert.deepEqual(await request.json(), { title: 'Cuộc trò chuyện mới', default_mode: 'STEP_BY_STEP' });
      return Response.json({ id: conversationId }, { status: 201 });
    },
    [sendKey]: async request => {
      assert.equal(request.headers.get('Accept'), 'text/event-stream');
      assert.deepEqual(await request.json(), { mode: 'STEP_BY_STEP', operation, ...(operation === 'ASK' ? { text: 'Câu hỏi' } : {}) });
      if (status === 'LOCKED') return errorResponse(403, 'ASSESSMENT_LOCKED');
      if (status === 'NO_FINAL') return new Response(start, { headers: streamHeaders });
      const message = { ...assistant, status, rendered: status === 'COMPLETED' ? assistant.rendered : null };
      const payload = new TextEncoder().encode(start + event('message', message));
      const body = new ReadableStream<Uint8Array>({ start(controller) {
        for (let i = 0; i < payload.length; i += 7) controller.enqueue(payload.slice(i, i + 7));
        controller.close();
      } });
      return new Response(body, { headers: streamHeaders });
    },
  });
  assert.equal((await h.ai.tutor.createConversation(classId, '  ')).id, conversationId);
  const chunks: string[] = [];
  const input = { classId, conversationId, content: 'Câu hỏi', mode: 'STEP_BY_STEP' as const, onChunk: (chunk: string) => { chunks.push(chunk); } };
  const complete = await h.ai.tutor.sendMessage(input);
  assert.deepEqual(chunks, [complete.content]);
  assert.equal(complete.stepNumber, 2);
  assert.equal(complete.totalSteps, 4);
  status = 'STOPPED';
  const stopped = await h.ai.tutor.sendMessage(input);
  assert.equal(stopped.isIncomplete, true);
  assert.match(stopped.content, /đã dừng/);
  status = 'FAILED';
  assert.equal((await h.ai.tutor.sendMessage(input)).isFailed, true);
  status = 'LOCKED';
  await assert.rejects(h.ai.tutor.sendMessage(input), { code: 'ASSESSMENT_LOCKED', status: 403 });
  status = 'NO_FINAL';
  await assert.rejects(h.ai.tutor.sendMessage(input), { code: 'INVALID_RESPONSE', status: 502 });
  status = 'COMPLETED';
  operation = 'HINT';
  await h.ai.tutor.sendMessage({ ...input, operation: 'HINT' });
});

for (const early of [true, false]) {
  test(`Tutor Stop uses the real turn ID when requested ${early ? 'before' : 'after'} start`, async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({ start(value) { controller = value; } });
    const h = harness({
      [sendKey]: () => new Response(body, { headers: streamHeaders }),
      [aiKey('POST', stopPath)]: () => {
        controller.enqueue(encoder.encode(event('message', { ...assistant, rendered: null, status: 'STOPPED' })));
        controller.close();
        return Response.json({ stopped: true });
      },
    });
    await h.ai.tutor.stopStreaming(conversationId);
    assert.equal(h.calls.length, 0);
    const answering = h.ai.tutor.sendMessage({ classId, conversationId, content: 'Câu hỏi', mode: 'STEP_BY_STEP' });
    if (!early) {
      controller.enqueue(encoder.encode(start));
      await new Promise<void>(resolve => setImmediate(resolve));
    }
    const stopped = h.ai.tutor.stopStreaming(conversationId);
    if (early) controller.enqueue(encoder.encode(start));
    const [message] = await Promise.all([answering, stopped]);
    assert.equal(message.isIncomplete, true);
    assert.equal(h.calls.filter(call => call === aiKey('POST', stopPath)).length, 1);
  });
}

test('SSE client rejects wrong media types and aborts a stalled stream', async () => {
  const options = { pathParams, body: { text: 'Câu hỏi' } };
  const mismatch = createAiHttpClient(() => null, async () => Response.json({}));
  await assert.rejects(mismatch('sendTutorMessage', options), { code: 'CONTRACT_MISMATCH' });
  const controller = new AbortController();
  let cancelled = false;
  const body = new ReadableStream<Uint8Array>({ cancel() { cancelled = true; } });
  const request = createAiHttpClient(() => null, async () => new Response(body, { headers: streamHeaders }));
  const promise = request('sendTutorMessage', { ...options, signal: controller.signal });
  await Promise.resolve();
  controller.abort();
  await assert.rejects(promise, { name: 'AbortError' });
  assert.equal(cancelled, true);
});

test('AI authentication clears unauthorized sessions and withholds SSE events after the session changes', async () => {
  const unauthorized = harness({ [listKey]: () => errorResponse(401, 'UNAUTHORIZED') });
  await assert.rejects(unauthorized.ai.workspace.load('s.tutor', { classId }), { status: 401 });
  assert.equal(unauthorized.lost(), 1);
  assert.equal(await unauthorized.session.identity.restore(), null);
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(value) { controller = value; } });
  const h = harness({ [sendKey]: () => new Response(body, { headers: streamHeaders }) });
  const chunks: string[] = [];
  const result = h.ai.tutor.sendMessage({ classId, conversationId, mode: 'STEP_BY_STEP', content: 'Câu hỏi', onChunk: chunk => chunks.push(chunk) });
  const refused = assert.rejects(result, { code: 'SESSION_CHANGED', status: 401 });
  await new Promise<void>(resolve => setImmediate(resolve));
  await h.session.identity.logout();
  controller.enqueue(new TextEncoder().encode(start + event('message', assistant)));
  controller.close();
  await refused;
  assert.deepEqual(chunks, []);
});

test('teacher Tutor workspace reads only config and saves policy using expected_version', async () => {
  const policy = { allow_general_knowledge: true, hint_reveal_after: 3, allow_solution_reveal: false,
    guidance_level: 'balanced', max_answer_words: 120 };
  let version: number | null = null;
  const h = harness({
    [aiKey('GET', configPath)]: () => Response.json({ ...policy, version }),
    [aiKey('PUT', configPath)]: async request => {
      const body = await request.json();
      if (body.expected_version !== version) return errorResponse(409, 'CONFLICT');
      assert.deepEqual(body, { ...policy, guidance_level: 'strong', expected_version: version });
      version = (version ?? 0) + 1;
      return Response.json({ ...policy, guidance_level: 'strong', version });
    },
  }, ['LECTURER']);
  const load = () => h.ai.workspace.load('t.tutor', { classId });
  const initial = await load();
  if (initial.kind !== 'ready' || !initial.teacherTutorConfig?.policy) assert.fail('Missing teacher policy');
  const initialPolicy = initial.teacherTutorConfig.policy;
  assert.equal(initialPolicy.version, null);
  assert.equal(initialPolicy.allowSolutionReveal, false);
  const { version: expectedVersion, ...values } = initialPolicy;
  const update = h.ai.tutor.updatePolicyConfig!;
  const payload = { ...values, guidanceLevel: 'strong' as const, expectedVersion };
  assert.equal((await update(classId, payload)).policy?.version, 1);
  const loaded = await load();
  assert.equal(loaded.kind === 'ready' && loaded.teacherTutorConfig?.policy?.version, 1);
  await assert.rejects(update(classId, payload), { status: 409, code: 'CONFLICT' });
  assert.ok(h.calls.every(path => !path.includes('/conversations')));
});

const practiceEditor = {
  revision: '1', defaultPoints: 2, questionTypes: ['single_choice', 'multiple_choice', 'true_false', 'short_answer'],
  editor: {
    practiceId, classId, className: 'Toán', title: 'Luyện tập', topic: 'Đại số', versionLabel: 'V1',
    currentVersionNumber: 1, isDraft: true, questions: [], reviewedCount: 0, totalQuestions: 0, canPublish: false,
    availableClassSources: [{ id: documentId, title: 'Bài giảng', version: 2, isReady: true, documentVersionId, sourceId }],
  },
};
const assessmentDraft = {
  class_id: classId, assessment_id: assessmentId, revision: 1, status: 'DRAFT', version_number: 1,
  settings: {}, questions: [], rejection_reason: null, can_approve: true,
  available_sources: [{ document_id: documentId, title: 'Bài giảng', version: 2, accessible: true,
    document_version_id: documentVersionId, source_id: sourceId, locator: '' }],
};
const itemByType = {
  single_choice: { type: 'single_choice', stem: 'Tính tổng.', correct_answer: '4', distractors: [{ text: '5' }] },
  multiple_choice: { type: 'multiple_choice', stem: 'Chọn các giá trị.', correct_answers: ['4', '5'], distractors: [{ text: '9' }] },
  true_false: { type: 'true_false_4', context: 'Xét các mệnh đề.', statements: [
    { text: 'Một', truth: true }, { text: 'Hai', truth: false }, { text: 'Ba', truth: false }, { text: 'Bốn', truth: true },
  ] },
  short_answer: { type: 'short_answer', stem: 'Tính giá trị.', answer: { kind: 'number', value: '0.5', accepted: ['1/2'], tolerance: 0.02 } },
};
const practicePath = classPath + `/practices/${practiceId}/editor`;
const assessmentPath = classPath + `/assessments/${assessmentId}/draft`;
const quizJobs = quizPath + '/jobs';
function quizHarness(type: keyof typeof itemByType, purpose: 'PRACTICE' | 'ASSESSMENT_DRAFT',
  result: 'usable' | 'revoked' | 'failed' = 'usable', proposedCount?: number) {
  let polls = 0;
  const count = purpose === 'PRACTICE' ? 1 : 2;
  const section = ['Đại số', 'Tổng'];
  const scope = [{ document_version_id: documentVersionId }];
  const item = { ...itemByType[type], evidence: [{ source_id: sourceId, source_spans: [{ start: 2, end: 10 }] }] };
  return harness({
    [learningKey('GET', practicePath)]: () => Response.json(practiceEditor),
    [learningKey('GET', assessmentPath)]: () => Response.json(assessmentDraft),
    [aiKey('POST', quizPath + '/proposals')]: async req => {
      assert.deepEqual(await req.json(), { purpose, scope, total: count });
      return Response.json(Array.from({ length: proposedCount ?? count }, (_, i) => ({ section, level: 'biet',
        type: i === 0 ? 'short_answer' : 'single_choice', count: 1 })));
    },
    [aiKey('POST', quizJobs)]: async req => {
      assert.deepEqual(await req.json(), { purpose, scope, matrix: [{ section, level: 'biet', type: item.type, count }] });
      return Response.json({ id: jobId, status: 'QUEUED' }, { status: 202 });
    },
    [aiKey('GET', quizJobs + '/' + jobId)]: () => Response.json({
      id: jobId, class_id: classId, status: ++polls === 1 ? 'RUNNING' : result === 'failed' ? 'FAILED' : 'COMPLETED',
    }),
    [aiKey('GET', quizJobs + '/' + jobId + '/items')]: () => Response.json([
      { id: randomUUID(), item: null }, { id: draftId, item: result === 'revoked' ? null : item },
    ]),
  }, ['LECTURER']);
}

for (const type of Object.keys(itemByType) as (keyof typeof itemByType)[]) {
  test(`Practice and Assessment convert AI ${type} into unreviewed questions (D1)`, async () => {
    const p = quizHarness(type, 'PRACTICE');
    const practiceScope = { classId, practiceId };
    const snapshot = await p.practice.load(practiceScope);
    assert.equal(snapshot.editor.availableClassSources[0].documentVersionId, documentVersionId);
    const proposed = await p.practice.propose({ ...practiceScope, expectedRevision: '1', sourceId: documentId, sourceVersion: 2, type });
    assert.equal(proposed.id, draftId);
    assert.equal(proposed.question.type, type);
    assert.equal(proposed.question.isReviewed, false);
    assert.equal(proposed.question.isAiProposed, true);
    assert.equal(proposed.question.points, 2);
    assert.equal(proposed.question.sourceRef?.documentId, documentId);
    assert.equal(p.calls.filter(call => call === learningKey('GET', practicePath)).length, 1);
    const a = quizHarness(type, 'ASSESSMENT_DRAFT');
    const questions = await a.assessment.generateQuestions({ classId, assessmentId, documentId, version: 2, type, instructions: '' });
    assert.equal(questions.length, 1);
    const [question] = questions;
    assert.equal(question.type, type);
    assert.equal(question.aiProposed, true);
    assert.equal(question.reviewed, false);
    assert.equal(question.source?.sourceId, sourceId);
    assert.equal(question.source?.documentVersionId, documentVersionId);
    assert.deepEqual(question.source?.wire?.source_spans, [{ start: 2, end: 10 }]);
    if (question.type === 'single_choice') assert.equal(question.key, 'opt-1');
    if (question.type === 'multiple_choice') assert.deepEqual(question.key, ['opt-1', 'opt-2']);
    if (question.type === 'true_false') assert.deepEqual(question.key, { 'stmt-1': true, 'stmt-2': false, 'stmt-3': false, 'stmt-4': true });
    if (question.type === 'short_answer') {
      assert.equal(question.key, '0.5');
      assert.deepEqual(question.acceptedAnswers, ['1/2']);
      assert.equal(question.tolerance, 0.02);
    }
    assert.equal(a.calls.filter(call => call === aiKey('GET', quizJobs + '/' + jobId)).length, 2);
  });
}

test('AI quiz surfaces failed jobs and revoked items without inventing a question', async () => {
  for (const scenario of ['failed', 'revoked'] as const) {
    const h = quizHarness('single_choice', 'PRACTICE', scenario);
    await assert.rejects(h.practice.propose({
      classId, practiceId, sourceId: documentVersionId, sourceVersion: 2, type: 'single_choice', expectedRevision: '1',
    }), scenario === 'failed' ? { status: 502, code: 'QUIZ_JOB_FAILED' } : { status: 503, code: 'UNAVAILABLE' });
    assert.equal(h.calls.some(call => call.endsWith('/items')), scenario === 'revoked');
  }
});

test('Assessment uses its loaded source and fills the requested matrix count when AI proposes one slot', async () => {
  const h = quizHarness('single_choice', 'ASSESSMENT_DRAFT', 'usable', 1);
  const snapshot = await h.assessment.load('t.assessmentEditor', { classId, assessmentId });
  assert.equal(snapshot.kind, 'ready');
  const result = await h.assessment.generateQuestions({
    classId, assessmentId, documentId, version: 2, instructions: '', type: 'single_choice',
  });
  assert.equal(result.length, 1);
  assert.equal(h.calls.filter(call => call === learningKey('GET', assessmentPath)).length, 1);
});

test('AI quiz polling is abortable before fetching items', async () => {
  const controller = new AbortController();
  const h = harness({
    [aiKey('POST', quizPath + '/proposals')]: () => Response.json([{ section: 'Đại số', level: 'biet', count: 1 }]),
    [aiKey('POST', quizJobs)]: () => Response.json({ id: jobId, status: 'QUEUED' }, { status: 202 }),
    [aiKey('GET', quizJobs + '/' + jobId)]: () => {
      controller.abort();
      return Response.json({ id: jobId, class_id: classId, status: 'RUNNING' });
    },
  }, ['LECTURER']);
  await assert.rejects(h.ai.generateQuizDrafts({ classId, documentVersionId, type: 'single_choice',
    purpose: 'PRACTICE', count: 1, signal: controller.signal }), { name: 'AbortError' });
  assert.ok(h.calls.every(call => !call.endsWith('/items')));
});

test('Student history workspace (s.history) loads read-only AI conversations and masks revoked citations', async () => {
  const revokedOnlyMessage = {
    id: messageId,
    role: 'ASSISTANT',
    status: 'COMPLETED',
    mode: 'STEP_BY_STEP',
    content: null,
    created_at: now,
    rendered: {
      status: 'source_access_revoked',
      claims: [],
      missing_parts: [],
      notice: 'Nội dung đã được ẩn do quyền truy cập nguồn thay đổi.',
    },
  };
  const h = harness({
    [listKey]: () => Response.json([conversation()]),
    [historyKey]: () => Response.json([
      { id: randomUUID(), role: 'USER', status: 'COMPLETED', mode: 'STEP_BY_STEP', content: 'Câu hỏi lịch sử', rendered: null, created_at: now },
      revokedOnlyMessage,
    ]),
  });
  const loaded = await h.ai.workspace.load('s.history', { classId });
  assert.equal(loaded.kind, 'ready');
  assert.equal(loaded.historyData?.conversations.length, 1);
  assert.equal(loaded.historyData?.conversations[0].messages[0].content, 'Câu hỏi lịch sử');
  assert.equal(loaded.historyData?.conversations[0].messages[1].content, 'Nội dung đã được ẩn do quyền truy cập nguồn thay đổi.');

  // When Core /classes/:classId returns 404 for an ended enrollment, readHistoryClass degrades to fallback summary
  const fallbackFetcher: typeof fetch = async (input, init) => {
    const request = new Request(new URL(String(input), web), init);
    const { pathname } = new URL(request.url);
    if (['/api/v1/auth/refresh', '/api/v1/auth/login'].includes(pathname)) {
      return Response.json({ access_token: 'test-token', token_type: 'bearer', expires_in: 60 });
    }
    if (pathname === '/api/v1/users/me') {
      return Response.json({ id: userId, school_id: schoolId, roles: ['STUDENT'], status: 'ACTIVE', display_name: 'Lumi', username: 'learner', email: null });
    }
    if (pathname === '/api/v1' + classPath) return errorResponse(404, 'NOT_FOUND');
    if (pathname === '/api/ai/v1' + conversationsPath) return Response.json([conversation()]);
    if (pathname === '/api/ai/v1' + messagesPath) return Response.json([revokedOnlyMessage]);
    return errorResponse(404, 'NOT_FOUND');
  };
  const fallbackSession = createLiveIdentitySession({ fetcher: fallbackFetcher });
  const fallbackAi = createLiveAi(fallbackSession, createLiveCore(fallbackSession));
  const fallbackLoaded = await fallbackAi.workspace.load('s.history', { classId });
  assert.equal(fallbackLoaded.kind, 'ready');
  assert.equal(fallbackLoaded.historyData?.summary.membershipStatus, 'ENDED');
  assert.equal(fallbackLoaded.historyData?.conversations[0].id, conversationId);
});

test('Tutor sendMessage forwards retry_of_message_id, CHECK/HINT/REVEAL payloads, and decodes HINT stepState', async () => {
  const retryUserId = randomUUID();
  const bodies: Record<string, unknown>[] = [];
  let nextStepState = 'HINT';
  const h = harness({
    [sendKey]: async request => {
      bodies.push(await request.json() as Record<string, unknown>);
      const payload = new TextEncoder().encode(
        start +
          event('message', {
            ...assistant,
            status: 'COMPLETED',
            rendered: {
              ...assistant.rendered,
              step: { state: nextStepState, step: 2, total_steps: 4 },
            },
          }),
      );
      return new Response(payload, { headers: streamHeaders });
    },
  });

  const hintMsg = await h.ai.tutor.sendMessage({
    classId,
    conversationId,
    content: '',
    mode: 'STEP_BY_STEP',
    operation: 'HINT',
  });
  assert.equal(hintMsg.stepState, 'HINT');

  nextStepState = 'IN_PROGRESS';
  const checked = await h.ai.tutor.sendMessage({
    classId,
    conversationId,
    content: 'x = 2',
    mode: 'STEP_BY_STEP',
    operation: 'CHECK',
  });
  assert.equal(checked.stepState, 'IN_PROGRESS');

  nextStepState = 'REVEALED';
  const revealed = await h.ai.tutor.sendMessage({
    classId,
    conversationId,
    content: '',
    mode: 'STEP_BY_STEP',
    operation: 'REVEAL',
  });
  assert.equal(revealed.stepState, 'REVEALED');

  await h.ai.tutor.sendMessage({
    classId,
    conversationId,
    content: 'Thử lại câu hỏi',
    mode: 'QUICK_QA',
    isRetry: true,
    retryUserMessageId: retryUserId,
  });

  assert.deepEqual(bodies, [
    { operation: 'HINT', mode: 'STEP_BY_STEP' },
    { operation: 'CHECK', mode: 'STEP_BY_STEP', text: 'x = 2' },
    { operation: 'REVEAL', mode: 'STEP_BY_STEP' },
    { operation: 'ASK', mode: 'QUICK_QA', text: 'Thử lại câu hỏi', retry_of_message_id: retryUserId },
  ]);
});
