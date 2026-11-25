import { routeHref } from '@/config/routes';
import { ApiError } from '../api/errors';
import type { PracticeActivity, PracticeAnswer, PracticeAttemptDetail, PracticeHistoryItem, PracticeQuestionFeedback, PracticeResultDetail, TeacherActivityItem } from '../domain';
import type { DataAdapter } from './port';
import type { LiveIdentitySession } from './live-identity';
import { createLivePracticeAuthoring, practiceQuestion, practiceSource } from './live-practice-authoring';
import { boolean, dictionary, enumeration, integer, invalid, learningReader, list, number, optional, questionType, readHistoryClass, record, requireLearningSession, text, timestamp, unavailableMessage, uuid, type LearningCore } from './learning-wire';

const practiceAttemptHref = (path: { classId: string; practiceId: string; attemptId: string }, result = false) =>
  routeHref(result ? 's.practiceResult' : 's.practiceAttempt', path) ?? invalid();

function score(value: unknown) {
  const row = record(value);
  return { correct: integer(row.correct), total: integer(row.total), percentage: number(row.percentage) };
}
function history(value: unknown, classId: string): PracticeHistoryItem {
  const row = record(value);
  const attemptId = uuid(row.attemptId), practiceId = uuid(row.practiceId);
  return {
    attemptId, practiceId, practiceTitle: text(row.practiceTitle), attemptNumber: integer(row.attemptNumber, 1),
    completedAt: timestamp(row.completedAt), score: score(row.score), durationMinutes: integer(row.durationMinutes),
    resultHref: practiceAttemptHref({ classId, practiceId, attemptId }, true),
  };
}
export function decodePracticeList(value: unknown) {
  const row = record(value);
  const classId = uuid(row.classId);
  return {
    classId, completedHistoryCount: integer(row.completedHistoryCount),
    history: list(row.history, item => history(item, classId)),
    activities: list(row.activities, (value): PracticeActivity => {
      const item = record(value);
      if (item.classId !== classId) return invalid();
      return {
        id: uuid(item.id), classId, title: text(item.title), topic: text(item.topic),
        questionCount: integer(item.questionCount), status: enumeration(item.status, 'NOT_STARTED', 'IN_PROGRESS', 'COMPLETED'),
        publishedVersionId: uuid(item.publishedVersionId), publishedVersionNumber: integer(item.publishedVersionNumber, 1),
        lastCompletedAt: optional(item.lastCompletedAt, timestamp), lastScore: optional(item.lastScore, score),
        activeAttemptId: optional(item.activeAttemptId, uuid), latestResultAttemptId: optional(item.latestResultAttemptId, uuid),
        currentQuestionIndex: integer(item.currentQuestionIndex), totalAttempts: integer(item.totalAttempts),
        history: list(item.history, entry => history(entry, classId)),
        activeAttemptVersionNumber: optional(item.activeAttemptVersionNumber, v => integer(v, 1)),
        activeAttemptQuestionCount: optional(item.activeAttemptQuestionCount, integer),
      };
    }),
  };
}
function feedback(value: unknown): PracticeQuestionFeedback {
  const row = record(value);
  return {
    questionId: text(row.questionId), isCorrect: boolean(row.isCorrect), feedbackTitle: text(row.feedbackTitle),
    explanation: text(row.explanation), sourceReference: optional(row.sourceReference, text),
    sourceRef: optional(row.sourceRef, practiceSource),
  };
}
function practiceAnswer(value: unknown, questionId: string): PracticeAnswer {
  const row = record(value);
  if (row.questionId != null && row.questionId !== questionId) return invalid();
  return {
    questionId, singleChoiceValue: optional(row.singleChoiceValue, text),
    multipleChoiceValues: optional(row.multipleChoiceValues, v => list(v, text)),
    trueFalseValues: optional(row.trueFalseValues, v => dictionary(v, boolean)),
    shortAnswerValue: optional(row.shortAnswerValue, text),
  };
}
function attemptBase(row: Record<string, unknown>) {
  return {
    classId: uuid(row.classId), practiceId: uuid(row.practiceId), attemptId: uuid(row.attemptId),
    ownerUserId: uuid(row.ownerUserId), className: text(row.className), title: text(row.title),
    publishedVersionId: uuid(row.publishedVersionId), publishedVersionNumber: integer(row.publishedVersionNumber, 1),
    startedAt: timestamp(row.startedAt),
  };
}
function practiceAttempt(value: unknown): PracticeAttemptDetail {
  const row = record(value);
  return {
    ...attemptBase(row), topic: text(row.topic), primaryTeacherName: text(row.primaryTeacherName),
    questions: list(row.questions, question => practiceQuestion(question, false)),
    savedAnswers: dictionary(row.savedAnswers, practiceAnswer), checkedFeedbacks: dictionary(row.checkedFeedbacks, feedback),
    lastSavedAt: timestamp(row.lastSavedAt), currentQuestionIndex: integer(row.currentQuestionIndex),
    isCompleted: boolean(row.isCompleted), completedAt: optional(row.completedAt, timestamp),
  };
}
function practiceResult(value: unknown): PracticeResultDetail {
  const row = record(value);
  return {
    ...attemptBase(row), attemptNumber: integer(row.attemptNumber, 1), completedAt: timestamp(row.completedAt),
    durationMinutes: integer(row.durationMinutes), score: score(row.score), correctCount: integer(row.correctCount),
    incorrectCount: integer(row.incorrectCount), unansweredCount: integer(row.unansweredCount),
    questions: list(row.questions, value => {
      const item = record(value);
      return {
        id: text(item.id), questionNumber: integer(item.questionNumber, 1), prompt: text(item.prompt),
        type: questionType(item.type), studentAnswerText: text(item.studentAnswerText),
        correctAnswerText: text(item.correctAnswerText), isCorrect: boolean(item.isCorrect),
        explanation: text(item.explanation), sourceReference: optional(item.sourceReference, text),
        sourceRef: optional(item.sourceRef, practiceSource),
      };
    }),
  };
}

function formatActivityDueLabel(raw: string): string {
  if (/^\d{4}-\d{2}-\d{2}T/.test(raw) && Number.isFinite(Date.parse(raw))) {
    return `Hạn: ${new Intl.DateTimeFormat('vi-VN', {
      dateStyle: 'short',
      timeStyle: 'short',
      timeZone: 'Asia/Ho_Chi_Minh',
    }).format(new Date(raw))}`;
  }
  return raw;
}

export function createTeacherActivityReader(session: LiveIdentitySession) {
  const read = learningReader(session);
  return (classId: string, kind?: TeacherActivityItem['type'], signal?: AbortSignal) =>
    read('activities', { pathParams: { classId }, query: { kind }, signal }, value =>
      list(record(value).items, (value): TeacherActivityItem => {
        const row = record(value);
        const resourceId = uuid(row.resourceId);
        const type = enumeration(row.type, 'PRACTICE', 'ASSESSMENT', 'ASSIGNMENT');
        const status = enumeration(row.status, 'DRAFT', 'PENDING_APPROVAL', 'PUBLISHED');
        if (row.classId !== classId || (kind && kind !== type)) return invalid();
        const editHref = type === 'PRACTICE' ? routeHref('t.practiceEditor', { classId, practiceId: resourceId })
          : type === 'ASSIGNMENT' ? routeHref('t.assignmentEditor', { classId, assignmentId: resourceId })
          : routeHref(status === 'PENDING_APPROVAL' ? 't.assessmentApproval' : 't.assessmentEditor', { classId, assessmentId: resourceId });
        return {
          id: uuid(row.id), resourceId, classId, type, status, title: text(row.title),
          topic: text(row.topic), dueOrTimeLabel: formatActivityDueLabel(text(row.dueOrTimeLabel)), editHref: editHref ?? undefined,
          pendingGradingCount: optional(row.pendingGradingCount, value => integer(value, 0)),
          canDeleteDraft: optional(row.canDeleteDraft, boolean),
          submissionsHref: type !== 'PRACTICE' && status === 'PUBLISHED'
            ? routeHref('t.gradingQueue', { classId }) ?? undefined : undefined,
        };
      }));
}

const workspaceRoutes = new Set(['s.practice', 's.practiceAttempt', 's.practiceResult', 't.activities', 't.practiceEditor', 't.practiceEdit']);

export function createLiveLearning(session: LiveIdentitySession, core: LearningCore, authoring: ReturnType<typeof createLivePracticeAuthoring>) {
  const read = learningReader(session);
  const activities = createTeacherActivityReader(session);
  const learning: DataAdapter['learning'] = {
    startPracticeAttempt: input => read('startPractice', { pathParams: input, body: { intent: input.intent ?? 'start' } }, value => {
      const attemptId = uuid(record(value).attempt_id);
      return { attemptId, href: practiceAttemptHref({ ...input, attemptId }) };
    }),
    checkPracticeAnswer: input => read('checkPracticeAnswer', {
      pathParams: input, body: { question_id: input.questionId, answer: input.answer },
    }, value => {
      const result = feedback(value);
      return result.questionId === input.questionId ? result : invalid();
    }),
    savePracticeDraft: input => read('savePracticeAttempt', {
      pathParams: input, body: { answers: input.answers, current_question_index: input.currentQuestionIndex },
    }, value => ({ savedAt: timestamp(record(value).saved_at) })),
    finishPracticeAttempt: input => read('finishPractice', { pathParams: input }, value => {
      const attemptId = uuid(record(value).attempt_id);
      if (attemptId !== input.attemptId) return invalid();
      return { attemptId, resultHref: practiceAttemptHref(input, true) };
    }),
    createPracticeDraft: input => read('createPractice', {
      pathParams: input, body: { title: input.title, topic: input.topic },
    }, value => {
      const practiceId = uuid(record(value).practice_id);
      return { practiceId, editHref: routeHref('t.practiceEditor', { classId: input.classId, practiceId }) ?? invalid() };
    }),
    async savePracticeEditorDraft(input) {
      const generation = session.sessionGeneration();
      const expectedRevision = input.expectedRevision ?? (await authoring.readSnapshot(input)).revision;
      requireLearningSession(session, generation);
      const { savedAt, snapshot } = await authoring.saveEditor({ ...input, expectedRevision });
      return { savedAt, reviewedCount: snapshot.editor.reviewedCount, canPublish: snapshot.editor.canPublish };
    },
    async publishPractice(input) {
      const generation = session.sessionGeneration();
      const expectedRevision = input.expectedRevision ?? (await authoring.readSnapshot(input)).revision;
      requireLearningSession(session, generation);
      const result = await authoring.publish({ ...input, expectedRevision });
      return { ...result, success: true, practiceId: input.practiceId };
    },
    async reviewEditorQuestion(input) {
      const generation = session.sessionGeneration();
      const expectedRevision = input.expectedRevision ?? (await authoring.readSnapshot(input)).revision;
      requireLearningSession(session, generation);
      const { editor } = await authoring.review({ ...input, expectedRevision });
      return { reviewedCount: editor.reviewedCount, canPublish: editor.canPublish };
    },
    deleteDraftActivity: input => read('deleteActivity', { pathParams: input }, () => undefined),
    copyActivity: input => read('copyActivity', {
      pathParams: { classId: input.classId, activityId: input.activityId },
      body: {
        target_class_id: input.targetClassId,
        ...(input.title?.trim() ? { title: input.title.trim() } : {}),
      },
    }, value => {
      const row = record(value);
      const id = uuid(row.activity_id ?? row.id);
      const classId = uuid(row.class_id);
      const kind = enumeration(row.kind, 'PRACTICE', 'ASSESSMENT', 'ASSIGNMENT');
      const editHref = kind === 'PRACTICE'
        ? routeHref('t.practiceEditor', { classId, practiceId: id })
        : kind === 'ASSIGNMENT'
          ? routeHref('t.assignmentEditor', { classId, assignmentId: id })
          : routeHref('t.assessmentEditor', { classId, assessmentId: id });
      if (!editHref) return invalid();
      return {
        id,
        activityId: id,
        classId,
        kind,
        title: text(row.title),
        status: enumeration(row.status, 'DRAFT'),
        editHref,
        strippedSourceCount: integer(row.stripped_source_count ?? 0, 0),
      };
    }),
  };
  const load: DataAdapter['workspace']['load'] = async (routeId, params, signal) => {
    const { classId, practiceId, attemptId } = params;
    try {
      if (routeId === 's.practice') {
        const [model, classSummary] = await Promise.all([
          read('practices', { pathParams: params, signal }, decodePracticeList), readHistoryClass(core, classId, signal),
        ]);
        if (model.classId !== classId) return invalid();
        return { kind: 'ready', classSummary, practiceList: { ...model, className: classSummary?.name, classSummary } };
      }
      if (routeId === 's.practiceAttempt' || routeId === 's.practiceResult') {
        const isResult = routeId === 's.practiceResult';
        const [model, classSummary] = await Promise.all([
          isResult ? read('practiceResult', { pathParams: params, signal }, practiceResult)
            : read('practiceAttempt', { pathParams: params, signal }, practiceAttempt),
          readHistoryClass(core, classId, signal),
        ]);
        if (model.classId !== classId || model.practiceId !== practiceId || model.attemptId !== attemptId) return invalid();
        return 'isCompleted' in model
          ? { kind: 'ready', classSummary, practiceAttempt: model }
          : { kind: 'ready', classSummary, practiceResult: model };
      }
      if (routeId === 't.activities') {
        const [classSummary, rows, classes] = await Promise.all([
          core.readClass(classId, signal), activities(classId, undefined, signal), core.readClasses(signal),
        ]);
        return {
          kind: 'ready', classSummary,
          teacherActivities: {
            classId, className: classSummary.name, classSummary, activities: rows,
            teacherRoleLabel: classSummary.teachingRole === 'PRIMARY' ? 'Giáo viên chính'
              : classSummary.teachingRole === 'ASSISTING' ? 'Giáo viên phối hợp' : undefined,
            assignedClasses: classes.filter(row => row.teachingRole).map(row => ({ id: row.id, name: row.name, isCurrent: row.id === classId, status: row.status })),
          },
        };
      }
      const snapshot = await authoring.load({ classId, practiceId }, signal);
      return { kind: 'ready', classSummary: snapshot.classSummary, teacherPracticeEditor: snapshot.editor };
    } catch (error) {
      if (error instanceof ApiError && error.status === 503) return { kind: 'unavailable', message: unavailableMessage };
      throw error;
    }
  };
  return { learning, activities, workspace: { load, handles: (routeId: string) => workspaceRoutes.has(routeId) } };
}
