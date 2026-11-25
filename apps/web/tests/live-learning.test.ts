import assert from 'node:assert/strict';
import test from 'node:test';
import { ApiError } from '../src/lib/api/errors';
import { createCoreProxy, createLearningProxy } from '../src/lib/api/proxy';
import { detectSubmissionPreviewKind, inferPreviewMimeType } from '../src/lib/assignment';
import { createLiveAi } from '../src/lib/data/live-ai';
import { createLiveAssessment } from '../src/lib/data/live-assessment';
import { createLiveAssignment } from '../src/lib/data/live-assignment';
import { createLiveCore, decodeCoreNotification } from '../src/lib/data/live-core';
import { createLiveIdentitySession } from '../src/lib/data/live-identity';
import { createLiveLearning } from '../src/lib/data/live-learning';
import { createLivePracticeAuthoring } from '../src/lib/data/live-practice-authoring';

const id = (number: number) => `00000000-0000-4000-8000-${String(number).padStart(12, '0')}`;
const classId = id(1), actorId = id(2), practiceId = id(3), attemptId = id(4), versionId = id(5);
const assessmentId = id(6), assignmentId = id(7), submissionId = id(8), uploadId = id(9);
const operationId = id(10), gradeId = id(11), revisionId = id(12), appealId = id(13);
const scope = { classId, practiceId, attemptId };
const assessmentScope = { classId, assessmentId, attemptId };
const assignmentScope = { classId, assignmentId };
const now = '2026-11-20T10:00:00Z';
const end = '2026-11-20T11:00:00Z';
const web = 'https://web.example';
const cls = `/classes/${classId}`;
const practices = `${cls}/practices`;
const practice = `${practices}/${practiceId}`;
const practiceAttempt = `${practice}/attempts/${attemptId}`;
const assessments = `${cls}/assessments`;
const assessment = `${assessments}/${assessmentId}`;
const assessmentAttempt = `${assessment}/attempts/${attemptId}`;
const assignments = `${cls}/assignments`;
const assignment = `${assignments}/${assignmentId}`;
const submissionGrading = `${assignment}/submissions/${submissionId}/grading`;
const history = `/history/results/${submissionId}`;
const questionId = 'q-1';
const error = (status: number) => Response.json({ error: { code: 'UPSTREAM_ERROR', message: 'Dịch vụ học tập chưa sẵn sàng.' } }, { status });

type Replies = Record<string, ((request: Request, url: URL) => Response | Promise<Response>) | undefined>;
function harness(replies: Replies, coreClassStatus = 200) {
  const calls: string[] = [];
  let lost = 0;
  const fetcher: typeof fetch = async (input, init) => {
    const url = new URL(String(input), web);
    const request = new Request(url, init);
    calls.push(`${request.method} ${url.pathname}${url.search}`);
    const auth = request.headers.get('Authorization');
    if (url.pathname === '/api/v1/auth/refresh' || url.pathname === '/api/v1/auth/login') {
      assert.equal(auth, null);
      return Response.json({ access_token: 'test-token', expires_in: 60, token_type: 'bearer' });
    }
    if (url.pathname === '/api/v1/auth/logout') return new Response(null, { status: 204 });
    assert.equal(auth, 'Bearer test-token');
    if (url.pathname === '/api/v1/users/me') {
      return Response.json({ id: actorId, status: 'ACTIVE', display_name: 'Lumi', school_id: id(21), roles: ['LECTURER'], username: 'learner', email: null });
    }
    const classRow = { id: classId, grade_level: 9, status: 'ACTIVE', name: 'Toán 9', enrollment_mode: 'AUTO', membership_status: 'ACTIVE', teaching_role: 'PRIMARY' };
    if (url.pathname === `/api/v1${cls}`) return coreClassStatus === 200 ? Response.json(classRow) : error(coreClassStatus);
    if (url.pathname === '/api/v1/classes') return Response.json({ items: [classRow], next_cursor: null });
    if (url.pathname === `/api/v1${cls}/calendar`) return Response.json({ items: [], next_cursor: null });
    if (url.pathname === `/api/v1${cls}/members`) {
      return replies[`GET ${cls}/members`]?.(request, url) ?? error(403);
    }
    assert.match(url.pathname, /^\/api\/learning\/v1\//);
    const key = `${request.method} ${url.pathname.replace(/^\/api\/learning\/v1/, '')}`;
    const respond = replies[key];
    if (!respond) throw new Error(`Unexpected request ${key}`);
    return respond(request, url);
  };
  const session = createLiveIdentitySession({ fetcher, onLost: () => { lost++; } });
  const core = createLiveCore(session);
  const ai = createLiveAi(session, core);
  const authoring = createLivePracticeAuthoring(session, core, ai);
  return {
    session, core, authoring, calls, lost: () => lost,
    ...createLiveLearning(session, core, authoring),
    assessment: createLiveAssessment(session, core, ai), assignment: createLiveAssignment(session, core),
  };
}
const json = (value: unknown, status = 200) => () => Response.json(value, { status });
const reply = (body: unknown, response: unknown, status = 200) => async (request: Request) => {
  assert.deepEqual(await request.json(), body);
  return Response.json(response, { status });
};
const proxyParts = (path: string) => path.slice(1).split('/').map(decodeURIComponent);
const requestTo = (path: string, options?: RequestInit) => new Request(`${web}/api/learning/v1${path}`, options);

test('Learning proxy checks CSRF and filters methods, segments, queries and headers', async () => {
  const calls: URL[] = [];
  const proxy = createLearningProxy(() => 'https://learning.example', async (input, init) => {
    calls.push(new URL(String(input)));
    const headers = new Headers(init?.headers);
    assert.equal(headers.get('Authorization'), 'Bearer test-token');
    assert.equal(headers.get('Cookie'), null);
    assert.equal(headers.get('X-Service-Key'), null);
    assert.equal(headers.get('X-Admin-Reason'), 'reason-code');
    assert.equal(headers.get('X-Correlation-ID'), operationId);
    assert.equal(headers.get('Accept'), 'application/json');
    assert.equal(init?.redirect, 'manual');
    return Response.json({ items: [] });
  }, () => web);
  const check = (path: string, method = 'GET', origin?: string, search = '') => proxy(
    requestTo(path + search, { method, headers: {
      ...(origin ? { Origin: origin } : {}), Authorization: 'Bearer test-token', Cookie: 'refresh=private',
      'X-Service-Key': 'private', 'X-Admin-Reason': 'reason-code', 'X-Correlation-ID': operationId,
    } }), proxyParts(path),
  );
  assert.equal((await check(`${practice}/publish`, 'POST', 'https://cross.example')).status, 403);
  assert.equal((await check(`${practice}/publish`, 'POST')).status, 403);
  for (const path of ['/internal/v1/quiz-items/import', '/classes/not-a-uuid/practices', `${practice}/questions/%2F/review`, `${practice}/questions/%2E%2E/review`]) {
    assert.equal((await check(path, path.includes('/review') ? 'POST' : 'GET', web)).status, 404);
  }
  assert.equal((await check(practices, 'PUT', web)).status, 404);
  assert.equal(calls.length, 0);
  assert.equal((await check(`${practice}/questions/c%C3%A2u%201/review`, 'POST', web)).status, 200);
  assert.equal(calls[0].pathname, `/api/v1${practice}/questions/c%C3%A2u%201/review`);
  assert.equal((await check(`${cls}/question-bank`, 'GET', undefined, '?type=single_choice&origin=AI&private=true')).status, 200);
  assert.equal(calls[1].search, '?origin=AI&type=single_choice');
  for (const path of [`${assessmentAttempt}/grading`, submissionGrading, `${assignment}/recipients`]) {
    assert.equal((await check(path)).status, 200);
    assert.equal(calls.at(-1)?.pathname, `/api/v1${path}`);
  }
});

test('Learning proxy preserves multipart bytes and download headers and isolates upstream failures', async () => {
  const bytes = Uint8Array.of(0, 255, 13, 10);
  const uploadPath = `${assignment}/uploads`;
  const form = new FormData();
  form.set('file', new File([bytes], 'bài.txt', { type: 'text/plain' }));
  form.set('expected_revision', '1');
  const request = requestTo(uploadPath, { method: 'POST', body: form, headers: { Origin: web, 'Idempotency-Key': operationId } });
  const raw = new Uint8Array(await request.clone().arrayBuffer());
  const downloadPath = `/submissions/${submissionId}/files/${uploadId}`;
  const attachment = "attachment; filename*=UTF-8''b%C3%A0i.txt";
  const proxy = createLearningProxy(() => 'https://learning.example', async (input, init) => {
    if (String(input).endsWith('/uploads')) {
      assert.ok(init?.body instanceof ArrayBuffer);
      assert.deepEqual(new Uint8Array(init.body), raw);
      const forwarded = new Request(input, init);
      assert.equal(forwarded.headers.get('Content-Type'), request.headers.get('Content-Type'));
      assert.equal(forwarded.headers.get('Idempotency-Key'), operationId);
      const parsed = await forwarded.formData();
      assert.equal(parsed.get('expected_revision'), '1');
      assert.deepEqual(new Uint8Array(await (parsed.get('file') as File).arrayBuffer()), bytes);
      return Response.json({ upload_id: uploadId }, { status: 201 });
    }
    return new Response(bytes, { headers: {
      'Content-Type': 'application/octet-stream', 'Content-Disposition': attachment,
      'X-Content-Type-Options': 'nosniff', 'Set-Cookie': 'private=1',
    } });
  }, () => web);
  assert.equal((await proxy(request, proxyParts(uploadPath))).status, 201);
  const download = await proxy(requestTo(downloadPath), proxyParts(downloadPath));
  assert.deepEqual(new Uint8Array(await download.arrayBuffer()), bytes);
  assert.equal(download.headers.get('Content-Disposition'), attachment);
  assert.equal(download.headers.get('X-Content-Type-Options'), 'nosniff');
  assert.equal(download.headers.get('Set-Cookie'), null);
  assert.equal(download.headers.get('Cache-Control'), 'no-store');
  const unreachable: typeof fetch = async () => { throw new Error('unreachable'); };
  for (const origin of [undefined, 'https://learning.example']) {
    const isolated = createLearningProxy(() => origin, unreachable, () => web);
    assert.equal((await isolated(requestTo(practices), proxyParts(practices))).status, 503);
  }
  const core = createCoreProxy(() => 'https://core.example', async () => Response.json({ items: [], next_cursor: null }));
  assert.equal((await core(new Request(`${web}/api/v1/classes`), ['classes'])).status, 200);
});

const publicQuestion = {
  id: questionId, type: 'single_choice' as const, prompt: 'Hai cộng hai bằng bao nhiêu?', points: 1,
  options: [{ id: 'a', label: 'A', text: '4' }], topic: 'Số học',
};
const practiceAnswer = { questionId, singleChoiceValue: 'a' };
const practiceScore = { correct: 1, total: 1, percentage: 100 };
const questionFeedback = { questionId, isCorrect: true, feedbackTitle: 'Chính xác!', explanation: 'Hai cộng hai bằng bốn.', sourceRef: null, sourceReference: null };
const practiceBase = {
  classId, practiceId, attemptId, ownerUserId: actorId, publishedVersionId: versionId,
  publishedVersionNumber: 1, className: 'Toán 9', title: 'Cộng', startedAt: now,
};
const practiceList = {
  classId, completedHistoryCount: 0, history: [], activities: [{
    id: practiceId, classId, title: 'Cộng', topic: 'Số học', questionCount: 1, status: 'IN_PROGRESS',
    publishedVersionId: versionId, publishedVersionNumber: 1, lastCompletedAt: null, lastScore: null,
    activeAttemptId: attemptId, latestResultAttemptId: null, currentQuestionIndex: 0, totalAttempts: 1,
    history: [], activeAttemptVersionNumber: 1, activeAttemptQuestionCount: 1,
  }],
};

test('live Practice loads the pinned attempt, saves answers and decodes feedback and results', async () => {
  const h = harness({
    [`GET ${practices}`]: json(practiceList),
    [`POST ${practice}/attempts`]: reply({ intent: 'resume' }, {
      attempt_id: attemptId, published_version_id: versionId, published_version_number: 1, href: '/ignored',
    }, 201),
    [`GET ${practiceAttempt}`]: json({
      ...practiceBase, topic: 'Số học', primaryTeacherName: 'Lumi', questions: [{ ...publicQuestion, correctOptionId: 'a' }],
      savedAnswers: { [questionId]: practiceAnswer }, checkedFeedbacks: {}, lastSavedAt: now, currentQuestionIndex: 0,
      isCompleted: false, completedAt: null,
    }),
    [`POST ${practiceAttempt}/check`]: reply({ question_id: questionId, answer: practiceAnswer }, questionFeedback),
    [`PUT ${practiceAttempt}/draft`]: reply({ answers: { [questionId]: practiceAnswer }, current_question_index: 0 }, { saved_at: now }),
    [`POST ${practiceAttempt}/finish`]: json({ attempt_id: attemptId, completed_at: end, score: practiceScore }),
    [`GET ${practiceAttempt}/result`]: json({
      ...practiceBase, attemptNumber: 1, completedAt: end, durationMinutes: 60, score: practiceScore,
      correctCount: 1, incorrectCount: 0, unansweredCount: 0, questions: [{
        id: questionId, questionNumber: 1, prompt: publicQuestion.prompt, type: 'single_choice',
        studentAnswerText: '4', correctAnswerText: '4', isCorrect: true, explanation: questionFeedback.explanation,
        sourceReference: null, sourceRef: null,
      }],
    }),
  });
  const list = await h.workspace.load('s.practice', { classId });
  assert.equal(list.kind === 'ready' && list.practiceList?.activities[0]?.activeAttemptId, attemptId);
  assert.equal(list.kind === 'ready' && list.classSummary?.name, 'Toán 9');
  assert.equal((await h.learning.startPracticeAttempt({ ...scope, intent: 'resume' })).href, `/student${practiceAttempt.replace('/practices/', '/practice/')}`);
  const attempt = await h.workspace.load('s.practiceAttempt', scope);
  assert.equal(attempt.kind === 'ready' && attempt.practiceAttempt?.savedAnswers[questionId]?.singleChoiceValue, 'a');
  assert.equal(JSON.stringify(attempt).includes('correctOptionId'), false);
  assert.equal((await h.learning.checkPracticeAnswer({ ...scope, questionId, answer: practiceAnswer })).isCorrect, true);
  assert.equal((await h.learning.savePracticeDraft({ ...scope, answers: { [questionId]: practiceAnswer }, currentQuestionIndex: 0 })).savedAt, now);
  assert.match((await h.learning.finishPracticeAttempt(scope)).resultHref, /\/result$/);
  const result = await h.workspace.load('s.practiceResult', scope);
  assert.equal(result.kind === 'ready' && result.practiceResult?.score.percentage, 100);
  assert.equal(h.calls.filter(call => call === 'POST /api/v1/auth/refresh').length, 1);
});

test('live Practice authoring uses the versioned editor DTO, reviews and publishes', async () => {
  const question = { ...publicQuestion, correctOptionId: 'a', isReviewed: false };
  const snapshot = (revision: number, reviewed: boolean) => ({
    editor: {
      classId, practiceId, className: classId, title: 'Cộng', topic: 'Số học', versionLabel: 'Phiên bản 1',
      currentVersionNumber: 1, isDraft: true, questions: [{ ...question, isReviewed: reviewed }],
      reviewedCount: Number(reviewed), totalQuestions: 1, canPublish: reviewed, availableClassSources: [],
    }, revision: String(revision), defaultPoints: 1, questionTypes: ['single_choice', 'multiple_choice', 'true_false', 'short_answer'],
  });
  const h = harness({
    [`GET ${practice}/editor`]: json(snapshot(1, false)),
    [`POST ${practices}`]: reply({ title: 'Cộng', topic: 'Số học' }, { practice_id: practiceId, version_id: versionId, revision: 1, edit_href: '/ignored' }, 201),
    [`PUT ${practice}/draft`]: reply({ title: 'Cộng', topic: 'Số học', questions: [question], expected_revision: 1 },
      { ...snapshot(2, false), saved_at: now, reviewed_count: 0, can_publish: false }),
    [`POST ${practice}/questions/${questionId}/review`]: reply({ approved: true, expected_revision: 2 },
      { ...snapshot(3, true), reviewed_count: 1, can_publish: true }),
    [`POST ${practice}/publish`]: reply({ expected_revision: 3 }, { practice_id: practiceId, published_version_id: versionId, version_number: 1 }),
    [`GET ${cls}/activities`]: json({ items: [{ classId, resourceId: practiceId, id: practiceId, title: 'Cộng',
      topic: 'Số học', type: 'PRACTICE', status: 'DRAFT', dueOrTimeLabel: 'Không giới hạn', editHref: '/ignored' }], next_cursor: null }),
  });
  const initial = await h.authoring.load(scope);
  assert.equal(initial.editor.className, 'Toán 9');
  assert.equal(initial.revision, '1');
  assert.equal((await h.learning.createPracticeDraft({ classId, title: 'Cộng', topic: 'Số học' })).practiceId, practiceId);
  const content = { title: 'Cộng', topic: 'Số học', questions: [question] };
  const saved = await h.authoring.save({ ...scope, expectedRevision: initial.revision, content });
  assert.equal(saved.revision, '2');
  assert.deepEqual(await h.learning.savePracticeEditorDraft({ ...scope, expectedRevision: '1', ...content }),
    { savedAt: now, canPublish: false, reviewedCount: 0 });
  const reviewed = await h.authoring.review({ ...scope, questionId, expectedRevision: saved.revision, approved: true });
  assert.equal(reviewed.editor.canPublish, true);
  assert.deepEqual(await h.learning.reviewEditorQuestion({ ...scope, questionId, expectedRevision: '2', approved: true }), { canPublish: true, reviewedCount: 1 });
  assert.equal((await h.authoring.publish({ ...scope, expectedRevision: reviewed.revision })).publishedVersionId, versionId);
  assert.equal((await h.learning.publishPractice({ ...scope, expectedRevision: '3' })).publishedVersionId, versionId);
  await assert.rejects(h.authoring.propose({ ...scope, expectedRevision: '3', sourceId: id(20), sourceVersion: 1, type: 'single_choice' }),
    { status: 503, code: 'UNAVAILABLE', message: 'Nguồn tài liệu lớp chưa sẵn sàng để sinh câu hỏi AI.' });
  assert.ok(h.calls.every(call => !call.includes('/api/ai/v1/')));
  const activities = await h.workspace.load('t.activities', { classId });
  assert.equal(activities.kind === 'ready' && activities.teacherActivities?.activities[0]?.resourceId, practiceId);
  const editor = await h.workspace.load('t.practiceEditor', scope);
  assert.equal(editor.kind === 'ready' && editor.teacherPracticeEditor?.practiceId, practiceId);
});

test('Learning 503 preserves the shared session, while 401 clears it for both services', async () => {
  let status = 503;
  const h = harness({ [`GET ${practices}`]: () => error(status) });
  assert.equal((await h.workspace.load('s.practice', { classId })).kind, 'unavailable');
  assert.deepEqual(await h.core.readEvents(classId), []);
  assert.equal(h.lost(), 0);
  status = 401;
  await assert.rejects(h.workspace.load('s.practice', { classId }), (e: unknown) => e instanceof ApiError && e.status === 401);
  assert.equal(h.lost(), 1);
  assert.equal(await h.session.identity.restore(), null);
});

test('a late Learning acknowledgment cannot cross a logout boundary', async () => {
  let complete!: (response: Response) => void;
  let dispatched!: () => void;
  const requested = new Promise<void>(resolve => { dispatched = resolve; });
  const h = harness({ [`PUT ${practiceAttempt}/draft`]: () => {
    dispatched();
    return new Promise<Response>(resolve => { complete = resolve; });
  } });
  const saving = h.learning.savePracticeDraft({ ...scope, answers: {} });
  await requested;
  await h.session.identity.logout();
  complete(Response.json({ saved_at: now }));
  await assert.rejects(saving, (e: unknown) => e instanceof ApiError && e.code === 'SESSION_CHANGED' && e.outcome === 'unknown');
});

const settings = {
  title: 'Kiểm tra', topic: 'Số học', code: 'KT', opens_at: now, closes_at: end, duration_minutes: 40,
  max_attempts: 2, grading_policy: 'BEST', results_at: null, appeal_window_days: null,
};
const assessmentQuestion = {
  ...publicQuestion, key: 'a', statements: [], explanation: 'Lời giải', rubric: '', reviewed: true,
  ai_proposed: false, manual_grading_required: false, accepted_answers: [], tolerance: 0.000001, source: null,
};
const receipt = {
  id: attemptId, class_id: classId, assessment_id: assessmentId, attempt_id: attemptId, version_id: versionId,
  version_number: 1, reason: 'MANUAL', submitted_at: now, saved_answer_count: 1,
  total_question_count: 1, saved_revision: 1, result_released: false,
};
const attemptDto = {
  class_id: classId, assessment_id: assessmentId, attempt_id: attemptId, owner_id: actorId,
  title: settings.title, code: settings.code, version_id: versionId, version_number: 1, attempt_number: 1,
  questions: [publicQuestion], saved_answers: { [questionId]: { question_id: questionId, value: 'a' } },
  saved_revision: 1, saved_at: now, started_at: now, ends_at: end, server_now: now, status: 'IN_PROGRESS', receipt: null,
};
const resultDto = {
  class_id: classId, assessment_id: assessmentId, attempt_id: attemptId, title: settings.title,
  version_number: 1, released: false, receipt, effective_grade: null, grade_history: [], questions: [],
};
const grade = {
  id: gradeId, grade_id: gradeId, revision_id: revisionId, grade_version: 1, version: 1,
  score: 8, total_score: 8, max_score: 10, scores: null, feedback: 'Đã chấm', reason: '',
  teacher_id: id(31), teacher_name: 'Giáo viên', confirmed_at: now,
};
const appealDto = (targetType: string, targetId: string, status = 'OPEN') => ({
  id: appealId, class_id: classId, grade_id: gradeId, student_id: actorId, target_type: targetType, target_id: targetId,
  question_id: null, reason: 'Xin xem lại', status, created_at: now, resolution_note: null, resolved_by: null, resolved_at: null,
});

test('live Assessment uses server time and revisions, reconciles submission and masks unreleased results', async () => {
  const summary = {
    ...settings, id: assessmentId, version_id: versionId, version_number: 1,
    question_count: 1, total_points: 1, status: 'OPEN', used_attempts: 0, active_attempt_id: null, completed_attempt_id: null, can_start: true,
  };
  const h = harness({
    [`GET ${assessments}`]: json({ server_now: now, items: [summary] }),
    [`GET ${assessment}`]: json({ server_now: now, assessment: summary }),
    [`POST ${assessment}/attempts`]: json({ attempt_id: attemptId, version_id: versionId, ends_at: end, server_now: now }, 201),
    [`GET ${assessmentAttempt}`]: json({ ...attemptDto, questions: [assessmentQuestion] }),
    [`PUT ${assessmentAttempt}/answers`]: reply({ expected_revision: 0, answers: attemptDto.saved_answers }, attemptDto),
    [`POST ${assessmentAttempt}/submit`]: async request => {
      assert.equal(request.headers.get('Idempotency-Key'), operationId);
      assert.deepEqual(await request.json(), { operation_id: operationId, expected_revision: 1 });
      return error(503);
    },
    [`GET ${assessmentAttempt}/reconcile`]: (_, url) => {
      assert.equal(url.searchParams.get('operation_id'), operationId);
      return Response.json({ state: 'CONFIRMED', receipt });
    },
    [`GET ${assessmentAttempt}/result`]: json(resultDto),
  });
  const list = await h.assessment.load('s.assessments', { classId });
  assert.equal(list.kind === 'ready' && list.page.type === 'list' && list.page.serverNow, now);
  const detail = await h.assessment.load('s.assessment', assessmentScope);
  assert.equal(detail.kind === 'ready' && detail.page.type === 'detail' && detail.page.assessment.canStart, true);
  assert.equal((await h.assessment.start(assessmentScope)).attemptId, attemptId);
  const attempt = await h.assessment.load('s.examAttempt', assessmentScope);
  assert.equal(attempt.kind === 'ready' && attempt.page.type === 'attempt' && attempt.page.attempt.className, 'Toán 9');
  assert.equal(JSON.stringify(attempt).includes('"key"'), false);
  const saved = await h.assessment.saveAnswers({ ...assessmentScope, expectedRevision: 0, answers: { [questionId]: { questionId, value: 'a' } } });
  assert.equal(saved.savedRevision, 1);
  assert.deepEqual(await h.assessment.submit({ ...assessmentScope, expectedRevision: 1, operationId }), { state: 'UNKNOWN', operationId });
  const reconciled = await h.assessment.reconcileSubmission({ ...assessmentScope, operationId });
  assert.equal(reconciled.state === 'CONFIRMED' && reconciled.receipt.savedRevision, 1);
  const result = await h.assessment.load('s.examResult', assessmentScope);
  assert.equal(result.kind === 'ready' && result.page.type === 'result' && result.page.result.effectiveGrade, null);
});

test('live Assessment editor decodes approval DTOs and rejects malformed mutation ACKs', async () => {
  const row = {
    class_id: classId, assessment_id: assessmentId, version_id: versionId, revision: 1,
    version_number: 1, settings, questions: [assessmentQuestion], status: 'DRAFT', rejection_reason: null,
  };
  const h = harness({
    [`POST ${assessments}`]: json({ assessment_id: assessmentId, version_id: versionId, revision: 1 }, 201),
    [`GET ${assessment}/draft`]: json({ ...row, can_approve: true }),
    [`PUT ${assessment}/draft`]: async request => {
      const dto = await request.json();
      assert.equal(dto.expected_revision, 1);
      assert.equal(dto.settings.opens_at, new Date(now).toISOString());
      assert.equal(dto.questions[0].key, 'a');
      assert.equal(dto.questions[0].ai_proposed, false);
      return Response.json({ ...row, revision: 2 });
    },
    [`POST ${assessment}/questions/${questionId}/review`]: reply({ expected_revision: 2 }, { ...row, revision: '3' }),
    [`POST ${assessment}/propose`]: json({ ...row, revision: 4, status: 'PENDING_APPROVAL' }),
    [`POST ${assessment}/decision`]: reply({ expected_revision: 4, decision: 'APPROVE', reason: 'Đã duyệt' }, { ...row, revision: 5, status: 'PUBLISHED' }),
    [`POST ${assessment}/release-results`]: json({ released_count: 2 }),
  });
  assert.equal((await h.assessment.createDraft(classId)).assessmentId, assessmentId);
  const opened = await h.assessment.load('t.assessmentEditor', assessmentScope);
  if (opened.kind !== 'ready' || opened.page.type !== 'editor') return assert.fail('Missing editor');
  assert.equal(opened.page.draft.canApprove, true);
  const saved = await h.assessment.saveDraft({ ...opened.page.draft, expectedRevision: opened.page.draft.revision });
  assert.equal(saved.revision, 2);
  await assert.rejects(h.assessment.reviewQuestion({ ...assessmentScope, questionId, expectedRevision: 2 }),
    (err: unknown) => err instanceof ApiError && err.code === 'CONTRACT_MISMATCH' && err.outcome === 'unknown');
  assert.equal((await h.assessment.propose({ ...assessmentScope, expectedRevision: 3 })).status, 'PENDING_APPROVAL');
  assert.equal((await h.assessment.decide({ ...assessmentScope, expectedRevision: 4, decision: 'APPROVE', reason: 'Đã duyệt' })).status, 'PUBLISHED');
  assert.equal((await h.assessment.releaseResults!(assessmentScope)).releasedCount, 2);
});

const definition = {
  id: assignmentId, class_id: classId, assignment_id: assignmentId, version_id: versionId, revision: 2,
  version_number: 1, deadline_version: 1, status: 'PUBLISHED', title: 'Bài tập', topic: 'Số học',
  instructions: 'Nộp lời giải', due_at: end, source: null, appeal_window_days: null,
  policy: {
    max_versions: 2, allow_late: false, late_until: null, resubmit_until: null, graded_version: 'LATEST',
    max_bytes: null, accepted_types: ['text/plain'], new_enrollment: 'AUTO', appeal_window_days: null, max_score: 10,
  },
};
const fileDto = { id: uploadId, name: 'bài.txt', type: 'text/plain', size: 3 };
const submission = {
  id: submissionId, submission_id: submissionId, assignment_id: assignmentId, class_id: classId, result_id: submissionId,
  student_id: actorId, student_name: 'Lumi', number: 1, submission_number: 1, assignment_revision: 2, operation_id: operationId,
  submitted_at: now, file: fileDto, note: 'Bài của em', is_late: false, is_graded_version: true, grade: null, grade_history: [],
};

test('live Assignment sends multipart and idempotency keys, handles 204 and reads binary receipts', async () => {
  const h = harness({
    [`GET ${assignments}`]: json({ items: [definition] }),
    [`GET ${assignment}`]: json({ assignment: definition, submissions: [submission], can_submit: true, submit_blocked_reason: null }),
    [`POST ${assignment}/uploads`]: async request => {
      assert.match(request.headers.get('Content-Type') ?? '', /^multipart\/form-data; boundary=/);
      const fields = await request.formData();
      assert.equal(fields.get('expected_revision'), '2');
      assert.equal(await (fields.get('file') as File).text(), 'abc');
      return Response.json({ upload_id: uploadId, id: uploadId, assignment_id: assignmentId, file: fileDto, acknowledged_at: now, expires_at: end, preview_only: true }, { status: 201 });
    },
    [`POST ${assignment}/submissions`]: async request => {
      assert.equal(request.headers.get('Idempotency-Key'), operationId);
      assert.deepEqual(await request.json(), { upload_id: uploadId, expected_revision: 2, operation_id: operationId, note: submission.note });
      return Response.json(submission, { status: 201 });
    },
    [`GET ${assignment}/submissions/reconcile`]: (_, url) => Response.json(url.searchParams.get('operation_id') === operationId ? submission : null),
    [`GET /submissions/${submissionId}/files/${uploadId}`]: () => new Response('abc', {
      headers: { 'Content-Type': 'application/octet-stream', 'Content-Disposition': "attachment; filename*=UTF-8''b%C3%A0i.txt" },
    }),
    [`PUT ${assignment}/recipients`]: async request => {
      assert.deepEqual(await request.json(), { expected_revision: 2, student_ids: [actorId] });
      return new Response(null, { status: 204 });
    },
  });
  const list = await h.assignment.load('s.assignments', { classId });
  assert.equal(list.kind === 'list' && list.assignments[0]?.submissions[0]?.id, submissionId);
  assert.equal((await h.assignment.load('s.assignment', assignmentScope)).kind, 'detail');
  const uploaded = await h.assignment.upload({ ...assignmentScope, expectedRevision: 2, file: new File(['abc'], 'bài.txt', { type: 'text/plain' }) });
  assert.equal(uploaded.previewOnly, true);
  assert.equal((await h.assignment.submit({ ...assignmentScope, uploadId: uploaded.id, expectedRevision: 2, operationId, note: submission.note })).id, submissionId);
  assert.equal(await h.assignment.reconcile({ ...assignmentScope, operationId: id(19) }), null);
  assert.equal((await h.assignment.reconcile({ ...assignmentScope, operationId }))?.id, submissionId);
  const file = await h.assignment.readFile({ submissionId, fileId: uploadId });
  assert.deepEqual(file, { name: 'bài.txt', type: 'text/plain', bytes: new TextEncoder().encode('abc') });
  await h.assignment.setRecipients({ ...assignmentScope, expectedRevision: 2, studentIds: [actorId] });
});

test('live Assignment maps grade, history and appeal DTOs and preserves pending proposal semantics', async () => {
  const appeal = appealDto('ASSIGNMENT_SUBMISSION', submissionId);
  const proposal = {
    ...grade, id: revisionId, class_id: classId, target_type: 'ASSIGNMENT_SUBMISSION', target_id: submissionId,
    status: 'PROPOSED', base_version: 1, proposed_by: id(31), proposed_at: now, decision_reason: null, appeal_id: appealId,
    teacher_id: null, teacher_name: null, confirmed_at: null,
  };
  const h = harness({
    [`GET ${history}`]: json({ ...submission, limited_history: true, grade, grade_history: [grade], can_appeal: true, appeals: [] }),
    [`GET /history/results/${attemptId}`]: json({ ...resultDto, limited_history: true }),
    [`POST ${cls}/appeals`]: reply({ target_type: 'ASSIGNMENT_SUBMISSION', target_id: submissionId, reason: appeal.reason }, appeal, 201),
    [`POST /submissions/${submissionId}/grade`]: reply({ expected_grade_version: 0, score: 8, feedback: grade.feedback }, grade),
    [`POST /submissions/${submissionId}/grade-hint`]: json({ feedback: 'Kiểm tra phép tính', limitations: 'Chỉ gợi ý' }),
    [`GET ${cls}/grading`]: json({
      items: [{ id: submissionId, activity_id: assignmentId, class_id: classId, student_id: actorId, student_name: 'Lumi',
        kind: 'ASSIGNMENT', title: definition.title, submitted_at: now, number: 1, late: false, graded: true, grade_id: gradeId,
        grade_version: 1, href: '/ignored', membership_ended: true, released: true, effective_score: 8,
        feedback: grade.feedback, grade_history: [grade], appeal_id: appealId, appeal_reason: appeal.reason }],
      appeals: [appeal], proposals: [proposal],
    }),
    [`POST /grades/${gradeId}/revisions`]: reply({
      expected_grade_version: 1, total_score: 9, feedback: 'Cộng một điểm', reason: 'Cộng một điểm', appeal_id: appealId,
    }, { ...proposal, score: 9, total_score: 9 }, 201),
    [`POST /grade-revisions/${revisionId}/decision`]: reply({
      expected_grade_version: 1, decision: 'APPROVE', reason: 'Đã đối chiếu',
    }, { ...proposal, ...grade, id: revisionId, grade_version: 2, version: 2, status: 'APPROVED' }),
    [`POST ${cls}/appeals/${appealId}/resolve`]: reply({ resolution_note: 'Đã duyệt', linked_revision_id: revisionId },
      { ...appeal, status: 'RESOLVED', resolution_note: 'Đã duyệt' }),
  }, 403);
  const result = await h.assignment.load('s.historyResult', { resultId: submissionId });
  assert.equal(result.kind === 'history' && result.submission.grade?.score, 8);
  assert.equal(result.classSummary, undefined);
  assert.equal((await h.assessment.readOwnHistory(attemptId)).receipt.attemptId, attemptId);
  assert.equal((await h.assignment.appeal({ submissionId, reason: appeal.reason })).id, appealId);
  assert.equal((await h.assignment.grade({ submissionId, score: 8, feedback: grade.feedback, expectedGradeVersion: 0 })).version, 1);
  assert.equal((await h.assignment.gradeHint({ submissionId })).limitations, 'Chỉ gợi ý');
  assert.equal((await h.assignment.propose({ classId, appealId, score: 9, expectedGradeVersion: 1, reason: 'Cộng một điểm' })).status, 'PENDING');
  assert.equal((await h.assignment.decide({ proposalId: revisionId, approve: true, expectedGradeVersion: 1, reason: 'Đã đối chiếu' })).status, 'APPROVED');
  assert.equal((await h.assignment.resolveAppeal!({ classId, appealId, resolutionNote: 'Đã duyệt', linkedRevisionId: revisionId })).status, 'RESOLVED');
});

const queueEvidence = {
  class_id: classId, student_id: actorId, student_name: 'Lumi', submitted_at: now, number: 1,
  late: false, graded: true, grade_id: gradeId, grade_version: 1, membership_ended: false,
  released: true, effective_score: grade.score, feedback: grade.feedback, grade_history: [grade],
  appeal_id: appealId, appeal_reason: 'Xin xem lại',
};

test('live assessment grading and correction read confirmed evidence and a distinct pending proposal', async () => {
  const appeal = appealDto('ASSESSMENT_ATTEMPT', attemptId, 'PROPOSED');
  const assessmentGrade = { ...grade, total_score: 1, max_score: 1, scores: { [questionId]: 1 } };
  const proposal = {
    ...assessmentGrade, id: revisionId, class_id: classId, target_type: appeal.target_type, target_id: attemptId,
    status: 'PROPOSED', base_version: 1, proposed_by: id(31), proposed_at: now, decision_reason: null,
    appeal_id: appealId, teacher_id: null, teacher_name: null, confirmed_at: null,
    total_score: 0, scores: { [questionId]: 0 }, reason: 'Đối chiếu đáp án',
  };
  const graded = {
    ...queueEvidence, id: attemptId, activity_id: assessmentId, kind: 'ASSESSMENT', title: settings.title,
    effective_score: 1, grade_history: [assessmentGrade],
  };
  const ungraded = {
    ...graded, id: id(54), released: false, graded: false, effective_score: null,
    feedback: '', grade_history: [], appeal_id: null, appeal_reason: null,
  };
  const wire = {
    class_id: classId, assessment_id: assessmentId, attempt_id: attemptId, student_id: actorId, student_name: 'Lumi',
    title: settings.title, version_number: 1, released: true, grade_id: gradeId, grade_version: 1,
    effective_grade: assessmentGrade, grade_history: [assessmentGrade], pending_proposal: proposal,
    appeal_id: appealId, appeal_reason: appeal.reason, can_approve: true,
    questions: [{ question: assessmentQuestion, answer: attemptDto.saved_answers[questionId], score: 1, maximum: 1, can_grade: false, feedback: '' }],
  };
  const h = harness({
    [`GET ${assessmentAttempt}/grading`]: json(wire),
    [`GET ${cls}/grading`]: json({ items: [graded, ungraded], appeals: [appeal], proposals: [proposal] }),
  });
  assert.equal((await h.session.identity.restore())?.id, actorId);
  const exam = await h.assessment.load('t.examGrading', assessmentScope);
  if (exam.kind !== 'ready' || exam.page.type !== 'grading') return assert.fail('Missing assessment grading');
  assert.equal(exam.page.grading.questions[0]?.question.key, 'a');
  assert.equal(exam.page.grading.questions[0]?.answer?.value, 'a');
  assert.equal(exam.page.grading.effectiveGrade?.score, 1);
  assert.equal(exam.page.grading.pendingProposal?.scores[questionId], 0);
  assert.equal(exam.page.grading.canApprove, true);
  assert.deepEqual((await h.assessment.listSubmittedAttempts(classId)).map(row => [row.released, row.effectiveScore]),
    [[true, 1], [false, null]]);
  const [listed] = await h.assessment.listGrading(classId);
  assert.equal(listed.appealId, appealId);
  assert.equal(listed.gradeHistory[0]?.feedback, grade.feedback);
  assert.deepEqual(await h.assessment.getCorrectionTarget(assessmentScope), listed);
  for (const route of ['t.correction', 't.correctionApproval']) {
    const page = await h.assignment.load(route, { classId, appealId, proposalId: revisionId });
    if (page.kind !== 'appeal-review' || page.evidence.kind !== 'assessment') return assert.fail('Missing official evidence');
    assert.equal(page.evidence.grading.questions[0]?.question.explanation, 'Lời giải');
    assert.equal(page.canDecide, true);
    assert.equal(page.canPropose, false);
  }
});

test('live assessment grading submits manual scores and adopts the returned history', async () => {
  const manual = { ...assessmentQuestion, type: 'short_answer', key: null, rubric: 'Giải thích', manual_grading_required: true };
  const latest = { ...grade, version: 2, grade_version: 2, scores: { [questionId]: 1 }, total_score: 1, max_score: 1 };
  const wire = {
    class_id: classId, assessment_id: assessmentId, attempt_id: attemptId, student_id: actorId, student_name: 'Lumi',
    title: settings.title, version_number: 1, released: false, grade_id: gradeId, grade_version: 2,
    effective_grade: latest, grade_history: [latest], pending_proposal: null,
    appeal_id: null, appeal_reason: null, can_approve: false,
    questions: [{ question: manual, answer: null, score: 1, maximum: 1, can_grade: true, feedback: '' }],
  };
  const h = harness({
    [`POST ${assessmentAttempt}/grade`]: reply({ expected_grade_version: 1, scores: { [questionId]: 1 }, feedback: grade.feedback }, wire),
    [`GET ${assessmentAttempt}/grading`]: json(wire),
  });
  const saved = await h.assessment.gradeAttempt!({ ...assessmentScope, expectedGradeVersion: 1, scores: { [questionId]: 1 }, feedback: grade.feedback });
  assert.equal(saved.questions[0]?.canGrade, true);
  assert.equal(saved.gradeHistory?.[0]?.version, 2);
  await assert.rejects(h.assessment.getCorrectionTarget(assessmentScope), (e: unknown) => e instanceof ApiError && e.status === 409);
});

test('live assignment grading and correction include scoped submissions, files and approved history', async () => {
  const graded = { ...submission, grade, grade_history: [grade] };
  const previousId = id(55);
  const previous = { ...submission, id: previousId, submission_id: previousId, result_id: previousId, is_graded_version: false };
  const appeal = appealDto('ASSIGNMENT_SUBMISSION', submissionId);
  const proposal = {
    ...grade, id: revisionId, class_id: classId, target_type: appeal.target_type, target_id: submissionId,
    status: 'PROPOSED', base_version: 1, proposed_by: id(31), proposed_at: now, decision_reason: null,
    appeal_id: appealId, teacher_id: null, teacher_name: null, confirmed_at: null,
  };
  const h = harness({
    [`GET ${submissionGrading}`]: json({
      assignment: { ...definition, status: 'SUPERSEDED' }, submission: graded, submissions: [previous, graded],
      appeals: [appeal], teaching_role: 'PRIMARY',
    }),
    [`GET ${cls}/grading`]: json({
      items: [{ ...queueEvidence, id: submissionId, activity_id: assignmentId, kind: 'ASSIGNMENT', title: definition.title }],
      appeals: [appeal], proposals: [proposal],
    }),
  });
  assert.equal((await h.session.identity.restore())?.id, actorId);
  const page = await h.assignment.load('t.assignmentGrading', { ...assignmentScope, submissionId });
  if (page.kind !== 'grading') return assert.fail('Missing assignment grading');
  assert.deepEqual(page.submissions.map(row => row.id), [previousId, submissionId]);
  assert.equal(page.submission.file.name, fileDto.name);
  assert.equal(page.submission.gradeHistory[0]?.score, 8);
  assert.equal(page.teachingRole, 'PRIMARY');
  assert.equal(page.appeals[0]?.id, appealId);
  for (const route of ['t.correction', 't.correctionApproval']) {
    const loaded = await h.assignment.load(route, { classId, appealId, proposalId: revisionId });
    if (loaded.kind !== 'appeal-review' || loaded.evidence.kind !== 'assignment') return assert.fail('Missing assignment evidence');
    assert.equal(loaded.evidence.assignment.instructions, definition.instructions);
    assert.equal(loaded.evidence.submissions.length, 2);
    assert.equal(loaded.evidence.submission.grade?.score, 8);
    assert.equal(loaded.canDecide, true);
  }
});

test('live assignment editor merges recipient state with Core enrollment and works without roster names', async () => {
  const enrolled = id(56), retained = id(57);
  let missingRoster = false;
  let explicit = true;
  const h = harness({
    [`GET ${assignment}`]: json({ assignment: definition, submissions: [], can_submit: false, submit_blocked_reason: null }),
    [`GET ${assignment}/recipients`]: () => Response.json({
      class_id: classId, assignment_id: assignmentId, revision: definition.revision, default_assigned: !explicit,
      audience: explicit ? [{ user_id: actorId, assigned: true }, { user_id: retained, assigned: true }] : [],
    }),
    [`GET ${cls}/members`]: () => missingRoster ? error(403) : Response.json([
      { student_id: actorId, student_display_name: 'An' }, { student_id: enrolled, student_display_name: 'Bình' },
    ]),
  });
  async function openEditor() {
    const page = await h.assignment.load('t.assignmentEditor', assignmentScope);
    if (page.kind !== 'editor') return assert.fail('Missing assignment editor');
    return page;
  }
  assert.deepEqual((await openEditor()).audience, [
    { userId: actorId, assigned: true, displayName: 'An' },
    { userId: enrolled, assigned: false, displayName: 'Bình' },
    { userId: retained, assigned: true },
  ]);
  explicit = false;
  assert.ok((await openEditor()).audience.every(entry => entry.assigned));
  explicit = true;
  missingRoster = true;
  const fallback = await openEditor();
  assert.equal(fallback.audienceRosterAvailable, false);
  assert.deepEqual(fallback.audience, [{ userId: actorId, assigned: true }, { userId: retained, assigned: true }]);
});

test('submission preview detection handles PDF magic bytes, images, markdown and download fallback', () => {
  const pdfBytes = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x37]);
  assert.equal(inferPreviewMimeType('bai.bin', 'application/octet-stream', pdfBytes), 'application/pdf');
  assert.equal(detectSubmissionPreviewKind('bai.pdf', 'application/octet-stream', pdfBytes), 'pdf');
  assert.equal(inferPreviewMimeType('hinh.png', 'application/octet-stream'), 'image/png');
  assert.equal(detectSubmissionPreviewKind('hinh.png', 'application/octet-stream'), 'image');
  assert.equal(inferPreviewMimeType('ghi-chu.md', 'application/octet-stream'), 'text/markdown');
  assert.equal(detectSubmissionPreviewKind('ghi-chu.md', 'application/octet-stream'), 'text');
  assert.equal(detectSubmissionPreviewKind('bai.docx', 'application/octet-stream'), 'download');
});

test('deleteDraftActivity uses DELETE proxy allowlist and ASSIGNMENT_GRADED decodes deep link', async () => {
  const h = harness({
    [`DELETE ${cls}/activities/${assignmentId}`]: () => new Response(null, { status: 204 }),
  });
  await h.learning.deleteDraftActivity!({ classId, activityId: assignmentId });
  assert.ok(h.calls.includes(`DELETE /api/learning/v1${cls}/activities/${assignmentId}`));

  const studentNotification = decodeCoreNotification(
    {
      id: id(90),
      type: 'ASSIGNMENT_GRADED',
      read: false,
      created_at: now,
      payload_json: {
        class_id: classId,
        resource_type: 'assignment_submission',
        resource_id: submissionId,
      },
    },
    { id: actorId, roles: ['STUDENT'], status: 'ACTIVE', username: 'hs', display_name: 'Học sinh', email: null, school_id: id(21) },
    new Map([[classId, 'Toán 9']]),
  );
  assert.equal(studentNotification.category, 'ASSIGNMENT');
  assert.equal(studentNotification.targetHref, `/student/history/results/${submissionId}`);
});

test('copyActivity sends POST to proxy allowlist and decodes copied draft links + null schedule drafts', async () => {
  const targetClassId = id(99);
  const copiedActivityId = id(98);
  const h = harness({
    [`POST ${cls}/activities/${assignmentId}/copy`]: reply(
      { target_class_id: targetClassId, title: 'Bài tập Lớp 10A2' },
      {
        id: copiedActivityId,
        activity_id: copiedActivityId,
        class_id: targetClassId,
        source_activity_id: assignmentId,
        source_class_id: classId,
        kind: 'ASSIGNMENT',
        title: 'Bài tập Lớp 10A2',
        status: 'DRAFT',
        version_number: 1,
        revision: 1,
        edit_href: `/teacher/classes/${targetClassId}/assignments/${copiedActivityId}/edit`,
        stripped_source_count: 1,
      },
      201,
    ),
    [`GET ${assignment}`]: json({
      assignment: {
        ...definition,
        status: 'DRAFT',
        due_at: null,
        source: null,
        policy: { ...definition.policy, late_until: null, resubmit_until: null, new_enrollment: 'AUTO' },
      },
      submissions: [],
      can_submit: false,
      submit_blocked_reason: null,
    }),
    [`GET ${assignment}/recipients`]: json({
      class_id: classId,
      assignment_id: assignmentId,
      revision: definition.revision,
      default_assigned: true,
      audience: [],
    }),
    [`GET ${assessment}/draft`]: json({
      class_id: classId,
      assessment_id: assessmentId,
      version_id: versionId,
      revision: 1,
      version_number: 1,
      status: 'DRAFT',
      settings: {
        title: 'Kiểm tra 15p (10A2)',
        topic: 'Hàm số',
        code: '',
        opens_at: null,
        closes_at: null,
        results_at: null,
        duration_minutes: 15,
        max_attempts: 1,
        grading_policy: 'BEST',
      },
      questions: [{ ...assessmentQuestion, reviewed: true, source: null }],
      rejection_reason: null,
      available_sources: [],
      can_approve: true,
    }),
  });

  const copied = await h.learning.copyActivity!({
    classId,
    activityId: assignmentId,
    targetClassId,
    title: 'Bài tập Lớp 10A2',
  });
  assert.equal(copied.activityId, copiedActivityId);
  assert.equal(copied.classId, targetClassId);
  assert.equal(copied.status, 'DRAFT');
  assert.equal(copied.strippedSourceCount, 1);
  assert.equal(copied.editHref, `/teacher/classes/${targetClassId}/assignments/${copiedActivityId}/edit`);
  assert.ok(h.calls.includes(`POST /api/learning/v1${cls}/activities/${assignmentId}/copy`));

  const loadedAssignment = await h.assignment.load('t.assignmentEditor', assignmentScope);
  if (loadedAssignment.kind !== 'editor') return assert.fail('Expected assignment editor');
  assert.equal(loadedAssignment.assignment.dueAt, '');

  const loadedAssessment = await h.assessment.load('t.assessmentEditor', assessmentScope);
  if (loadedAssessment.kind !== 'ready' || loadedAssessment.page.type !== 'editor') return assert.fail('Expected assessment editor');
  assert.equal(loadedAssessment.page.draft.settings.opensAt, '');
  assert.equal(loadedAssessment.page.draft.settings.closesAt, '');
  assert.equal(loadedAssessment.page.draft.questions[0]?.reviewed, true);
});
