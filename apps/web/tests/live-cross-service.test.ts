import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import test from 'node:test';
import { ApiError } from '../src/lib/api/errors';
import { integrations } from '../src/lib/data/integrations';
import { createLiveAi } from '../src/lib/data/live-ai';
import { createLiveCore } from '../src/lib/data/live-core';
import { createLiveDashboardEnricher } from '../src/lib/data/live-dashboard';
import { createLiveDocument } from '../src/lib/data/live-document';
import { createLiveIdentitySession } from '../src/lib/data/live-identity';

const schoolId = randomUUID();
const studentId = randomUUID();
const teacherId = randomUUID();
const classId = randomUUID();
const secondClassId = randomUUID();
const docId = randomUUID();
const noteId = randomUUID();
const convId = randomUUID();
const msgId = randomUUID();
const assessmentId = randomUUID();
const assignmentId = randomUUID();
const practiceId = randomUUID();
const attemptId = randomUUID();

const now = new Date().toISOString();
const pastIso = new Date(Date.now() - 3_600_000).toISOString();
const futureIso = new Date(Date.now() + 86_400_000 * 2).toISOString();
const laterIso = new Date(Date.now() + 86_400_000 * 4).toISOString();

const classSummaryDto = (id: string, name: string, teachingRole: 'PRIMARY' | 'ASSISTING' | null) => ({
  id,
  school_id: schoolId,
  academic_year_id: randomUUID(),
  semester_id: randomUUID(),
  course_id: randomUUID(),
  homeroom_class_id: null,
  name,
  grade_level: 9,
  enrollment_mode: 'AUTO',
  status: 'ACTIVE',
  version: 1,
  teaching_role: teachingRole,
  student_count: 25,
  primary_teacher_name: 'Cô Lan',
});

function createHarness(
  role: 'STUDENT' | 'LECTURER',
  handlers: Record<string, () => Response | Promise<Response>> = {},
) {
  const actorId = role === 'LECTURER' ? teacherId : studentId;
  const userDto = {
    id: actorId,
    email: `${role.toLowerCase()}@example.com`,
    username: null,
    school_id: schoolId,
    display_name: role === 'LECTURER' ? 'Cô Lan' : 'Minh An',
    roles: [role],
    status: 'ACTIVE',
  };
  let lostCount = 0;
  const fetcher: typeof fetch = async (input, init) => {
    const url = new URL(String(input), 'https://web.example');
    const method = String(init?.method ?? 'GET');
    const key = `${method} ${url.pathname}`;
    if (handlers[key]) return handlers[key]();
    if (key === 'POST /api/v1/auth/login' || key === 'POST /api/v1/auth/refresh') {
      return Response.json({ token_type: 'bearer', access_token: 'access-token', expires_in: 900 });
    }
    if (key === 'GET /api/v1/users/me') return Response.json(userDto);
    if (key === 'GET /api/v1/classes') {
      const tr = role === 'LECTURER' ? 'PRIMARY' : null;
      return Response.json({
        items: [classSummaryDto(classId, 'Toán 9A', tr), classSummaryDto(secondClassId, 'Văn 9B', tr)],
        next_cursor: null,
      });
    }
    if (key === `GET /api/v1/classes/${classId}`) {
      return Response.json(classSummaryDto(classId, 'Toán 9A', role === 'LECTURER' ? 'PRIMARY' : null));
    }
    if (url.pathname.endsWith('/members')) return Response.json([{ student_id: studentId, student_display_name: 'Minh An' }]);
    if (url.pathname.endsWith('/calendar') || url.pathname.endsWith('/posts')) {
      return Response.json({ items: [], next_cursor: null });
    }
    return Response.json({ error: { code: 'NOT_FOUND', message: 'not found' } }, { status: 404 });
  };

  const session = createLiveIdentitySession({
    fetcher,
    onLost: () => {
      lostCount++;
    },
  });
  const core = createLiveCore(session);
  const doc = createLiveDocument(session, core.classroomReads);
  const ai = createLiveAi(session, core, {
    pollDelayMs: 0,
    documentContext: { listClassDocuments: doc.listClassDocuments, listClassNotes: doc.listClassNotes },
  });
  const dashboard = createLiveDashboardEnricher(session, { listClassDocuments: doc.listClassDocuments });

  return { session, core, doc, ai, dashboard, lostCount: () => lostCount };
}

function docDto(title = 'Đề cương Toán 9') {
  return {
    id: docId,
    school_id: schoolId,
    owner_user_id: teacherId,
    owner_display_name: 'Cô Lan',
    title,
    description: null,
    format: 'PDF',
    created_at: pastIso,
    updated_at: now,
    current_version: 2,
    serving_version: 2,
    page_count: 14,
  };
}

function noteDto() {
  return {
    id: noteId,
    user_id: studentId,
    class_id: classId,
    document_id: docId,
    document_title: 'Đề cương Toán 9',
    document_format: 'PDF',
    version_number: 2,
    locator: 'Trang 3',
    page_number: 3,
    is_source_accessible: true,
    source_quote: 'Hệ thức lượng',
    note_text: 'Ghi nhớ công thức đường cao',
    updated_at: now,
  };
}

function assessmentDto(title = 'Kiểm tra giữa kỳ') {
  return {
    id: assessmentId,
    title,
    code: 'GK1',
    topic: 'Hình học',
    opens_at: pastIso,
    closes_at: futureIso,
    duration_minutes: 45,
    max_attempts: 1,
    grading_policy: 'BEST',
    results_at: null,
    version_number: 1,
    question_count: 10,
    total_points: 10,
    status: 'OPEN',
    used_attempts: 0,
    active_attempt_id: null,
    completed_attempt_id: null,
    can_start: true,
  };
}

function assignmentDto(title = 'Bài tập chương 1') {
  return {
    id: assignmentId,
    assignment_id: assignmentId,
    class_id: classId,
    revision: 1,
    status: 'PUBLISHED',
    title,
    topic: 'Đại số',
    instructions: 'Hoàn thành bài tập',
    due_at: laterIso,
    source: null,
    policy: {
      max_versions: 2,
      allow_late: false,
      late_until: null,
      resubmit_until: null,
      graded_version: 'LATEST',
      max_bytes: null,
      accepted_types: ['.pdf'],
      new_enrollment: 'AUTO',
      max_score: 10,
    },
  };
}

function practicePayload() {
  return {
    classId,
    completedHistoryCount: 0,
    history: [],
    activities: [
      {
        id: practiceId,
        classId,
        title: 'Luyện tập hàm số',
        topic: 'Hàm số',
        questionCount: 5,
        status: 'IN_PROGRESS',
        publishedVersionId: randomUUID(),
        publishedVersionNumber: 1,
        activeAttemptId: attemptId,
        currentQuestionIndex: 2,
        totalAttempts: 1,
        history: [],
      },
    ],
  };
}

test('integrations marks learningDeadlineProjection as contracted', () => {
  assert.equal(integrations.calendar.operations.learningDeadlineProjection, 'contracted');
});

test('s.tutor deep-links citations to s.reader, loads Document classHasSources & quickNotes, and isolates 503 vs 401', async () => {
  let docsMode: 'ok' | 'empty' | '503' | '401' = 'ok';
  const h = createHarness('STUDENT', {
    [`GET /api/ai/v1/classes/${classId}/tutor/conversations`]: () =>
      Response.json([{ id: convId, class_id: classId, title: 'Ôn tập', default_mode: 'STEP_BY_STEP', updated_at: now }]),
    [`GET /api/ai/v1/tutor/conversations/${convId}/messages`]: () =>
      Response.json([
        {
          id: msgId,
          role: 'ASSISTANT',
          status: 'COMPLETED',
          mode: 'STEP_BY_STEP',
          content: null,
          created_at: now,
          rendered: {
            step: { step: 1, total_steps: 2 },
            claims: [
              {
                text: 'Xem lại phần hệ thức lượng.',
                basis: 'source',
                citations: [
                  {
                    document_id: docId,
                    source_id: randomUUID(),
                    quote: 'b^2 = a * b\'',
                    location: { document_name: 'Đề cương Toán 9', page: 3 },
                  },
                  {
                    source_id: 'legacy-source',
                    quote: 'Fallback citation',
                    location: { document_name: 'Tài liệu bổ sung', page: 2 },
                  },
                ],
              },
            ],
          },
        },
      ]),
    [`GET /api/learning/v1/classes/${classId}/assessments`]: () => Response.json({ items: [] }),
    [`GET /api/v1/classes/${classId}/documents`]: () => {
      if (docsMode === '503') return Response.json({ error: { code: 'UNAVAILABLE', message: 'down' } }, { status: 503 });
      if (docsMode === '401') return Response.json({ error: { code: 'UNAUTHENTICATED', message: 'expired' } }, { status: 401 });
      return Response.json({ items: docsMode === 'empty' ? [] : [docDto()], next_cursor: null });
    },
    'GET /api/v1/users/me/notes': () => {
      if (docsMode === '503') return Response.json({ error: { code: 'UNAVAILABLE', message: 'down' } }, { status: 503 });
      return Response.json({
        items: [
          noteDto(),
          { ...noteDto(), id: randomUUID(), class_id: secondClassId, note_text: 'Lớp khác' },
        ],
        next_cursor: null,
      });
    },
  });

  const ready = await h.ai.workspace.load('s.tutor', { classId });
  assert.equal(ready.kind, 'ready');
  if (ready.kind !== 'ready') return;
  const tutor = ready.tutorWorkspace!;
  assert.equal(tutor.classHasSources, true);
  assert.equal(tutor.quickNotes.length, 1);
  assert.equal(tutor.quickNotes[0].id, noteId);
  assert.equal(tutor.citations[0].readerHref, `/student/classes/${classId}/documents/${docId}?page=3`);
  assert.equal(tutor.citations[1].readerHref, `/student/classes/${classId}/documents`);

  docsMode = 'empty';
  const noSources = await h.ai.workspace.load('s.tutor', { classId });
  assert.equal(noSources.kind === 'ready' && noSources.tutorWorkspace?.classHasSources, false);

  docsMode = '503';
  const degraded = await h.ai.workspace.load('s.tutor', { classId });
  assert.equal(degraded.kind, 'ready');
  if (degraded.kind === 'ready') {
    assert.equal(degraded.tutorWorkspace?.classHasSources, true);
    assert.deepEqual(degraded.tutorWorkspace?.quickNotes, []);
  }

  docsMode = '401';
  await assert.rejects(h.ai.workspace.load('s.tutor', { classId }), (err: unknown) => err instanceof ApiError && err.status === 401);
  assert.equal(h.lostCount(), 1);
});

test('s.overview and s.home enrich live materials, upcoming activities, recent studies & tutorSuggestions with 503 fault isolation', async () => {
  let failLearning = false;
  let failDoc = false;
  const h = createHarness('STUDENT', {
    [`GET /api/v1/classes/${classId}/documents`]: () =>
      failDoc
        ? Response.json({ error: { code: 'UNAVAILABLE', message: 'down' } }, { status: 503 })
        : Response.json({ items: [docDto()], next_cursor: null }),
    [`GET /api/learning/v1/classes/${classId}/assessments`]: () =>
      failLearning
        ? Response.json({ error: { code: 'UNAVAILABLE', message: 'down' } }, { status: 503 })
        : Response.json({ items: [assessmentDto()] }),
    [`GET /api/learning/v1/classes/${classId}/assignments`]: () =>
      failLearning
        ? Response.json({ error: { code: 'UNAVAILABLE', message: 'down' } }, { status: 503 })
        : Response.json({ items: [assignmentDto()] }),
    [`GET /api/learning/v1/classes/${classId}/practices`]: () =>
      failLearning
        ? Response.json({ error: { code: 'UNAVAILABLE', message: 'down' } }, { status: 503 })
        : Response.json(practicePayload()),
    [`GET /api/learning/v1/classes/${secondClassId}/assessments`]: () => Response.json({ items: [] }),
    [`GET /api/learning/v1/classes/${secondClassId}/assignments`]: () => Response.json({ items: [] }),
    [`GET /api/learning/v1/classes/${secondClassId}/practices`]: () =>
      Response.json({ classId: secondClassId, completedHistoryCount: 0, history: [], activities: [] }),
  });

  const baseOverview = await h.core.workspace.load('s.overview', { classId });
  const overview = await h.dashboard.enrich('s.overview', { classId }, baseOverview);
  assert.equal(overview.kind, 'ready');
  if (overview.kind === 'ready') {
    assert.equal(overview.classOverview?.recentMaterials[0].id, docId);
    assert.equal(overview.classOverview?.recentMaterials[0].href, `/student/classes/${classId}/documents/${docId}`);
    assert.equal(overview.classOverview?.upcomingActivities.length, 3);
    assert.deepEqual(
      overview.classOverview?.upcomingActivities.map(item => item.type),
      ['EXAM', 'ASSIGNMENT', 'PRACTICE'],
    );
  }

  const baseHome = await h.core.workspace.load('s.home', {});
  const home = await h.dashboard.enrich('s.home', {}, baseHome);
  assert.equal(home.kind, 'ready');
  if (home.kind === 'ready') {
    assert.equal(home.studentHome?.upcomingTasks.length, 2);
    assert.equal(home.studentHome?.upcomingTasks[0].type, 'EXAM');
    assert.equal(home.studentHome?.upcomingTasks[1].type, 'ASSIGNMENT');
    assert.equal(home.studentHome?.recentStudies[0].progressPercent, 60);
    assert.equal(
      home.studentHome?.recentStudies[0].href,
      `/student/classes/${classId}/practice/${practiceId}/attempts/${attemptId}`,
    );
    assert.equal(home.studentHome?.tutorSuggestions.length, 2);
  }

  failDoc = true;
  failLearning = true;
  const degraded = await h.dashboard.enrich('s.overview', { classId }, await h.core.workspace.load('s.overview', { classId }));
  assert.equal(degraded.kind, 'ready');
  if (degraded.kind === 'ready') {
    assert.deepEqual(degraded.classOverview?.recentMaterials, []);
    assert.deepEqual(degraded.classOverview?.upcomingActivities, []);
  }
});

test('t.overview and t.home enrich activities & pendingGrading metrics and keep metrics undefined on Learning 503', async () => {
  let mode: 'ok' | '503' | '401' = 'ok';
  const queueResponse = (targetClassId: string, ungradedCount: number, openAppealCount: number) =>
    Response.json({
      items: Array.from({ length: ungradedCount }, () => ({
        id: randomUUID(),
        kind: 'ASSIGNMENT',
        class_id: targetClassId,
        activity_id: assignmentId,
        student_id: studentId,
        student_name: 'Minh An',
        title: 'Bài tập',
        submitted_at: now,
        number: 1,
        late: false,
        graded: false,
        membership_ended: false,
        grade_id: null,
        grade_version: 0,
        released: false,
        effective_score: null,
        feedback: '',
        grade_history: [],
        appeal_id: null,
        appeal_reason: null,
      })),
      appeals: Array.from({ length: openAppealCount }, () => ({
        id: randomUUID(),
        class_id: targetClassId,
        student_id: studentId,
        grade_id: randomUUID(),
        target_type: 'ASSIGNMENT_SUBMISSION',
        target_id: randomUUID(),
        question_id: null,
        reason: 'Xem lại',
        status: 'OPEN',
        created_at: now,
      })),
      proposals: [],
    });

  const h = createHarness('LECTURER', {
    [`GET /api/learning/v1/classes/${classId}/activities`]: () => {
      if (mode === '503') return Response.json({ error: { code: 'UNAVAILABLE', message: 'down' } }, { status: 503 });
      if (mode === '401') return Response.json({ error: { code: 'UNAUTHENTICATED', message: 'expired' } }, { status: 401 });
      return Response.json({
        items: [
          {
            id: randomUUID(),
            resourceId: assessmentId,
            classId,
            type: 'ASSESSMENT',
            status: 'PUBLISHED',
            title: 'Kiểm tra giữa kỳ',
            topic: 'Hình học',
            dueOrTimeLabel: 'Còn 2 ngày',
          },
          {
            id: randomUUID(),
            resourceId: practiceId,
            classId,
            type: 'PRACTICE',
            status: 'DRAFT',
            title: 'Nháp luyện tập',
            topic: 'Đại số',
            dueOrTimeLabel: 'Bản nháp',
          },
        ],
      });
    },
    [`GET /api/learning/v1/classes/${classId}/grading`]: () =>
      mode === '503'
        ? Response.json({ error: { code: 'UNAVAILABLE', message: 'down' } }, { status: 503 })
        : queueResponse(classId, 2, 1),
    [`GET /api/learning/v1/classes/${secondClassId}/grading`]: () =>
      mode === '503'
        ? Response.json({ error: { code: 'UNAVAILABLE', message: 'down' } }, { status: 503 })
        : queueResponse(secondClassId, 1, 0),
  });

  const overview = await h.dashboard.enrich('t.overview', { classId }, await h.core.workspace.load('t.overview', { classId }));
  assert.equal(overview.kind, 'ready');
  if (overview.kind === 'ready') {
    assert.deepEqual(overview.teacherMetrics, { pendingGrading: 3, openActivities: 1 });
    assert.equal(overview.classOverview?.upcomingActivities.length, 2);
    assert.equal(overview.classOverview?.upcomingActivities[0].type, 'EXAM');
  }

  const home = await h.dashboard.enrich('t.home', {}, await h.core.workspace.load('t.home', {}));
  assert.equal(home.kind, 'ready');
  if (home.kind === 'ready') {
    assert.equal(home.teacherHome?.stats?.pendingGrading, 4);
  }

  mode = '503';
  const degradedHome = await h.dashboard.enrich('t.home', {}, await h.core.workspace.load('t.home', {}));
  assert.equal(degradedHome.kind === 'ready' && degradedHome.teacherHome?.stats?.pendingGrading, undefined);

  const degradedOverview = await h.dashboard.enrich('t.overview', { classId }, await h.core.workspace.load('t.overview', { classId }));
  assert.equal(degradedOverview.kind === 'ready' && degradedOverview.teacherMetrics, undefined);

  mode = '401';
  await assert.rejects(
    h.dashboard.enrich('t.overview', { classId }, await h.core.workspace.load('t.overview', { classId })),
    (err: unknown) => err instanceof ApiError && err.status === 401,
  );
  assert.equal(h.lostCount(), 1);
});
