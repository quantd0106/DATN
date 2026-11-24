import { ApiError } from '../api/errors';
import type { AssessmentAnswer, AssessmentAttempt, AssessmentDraft, AssessmentQuestion, AssessmentReceipt, AssessmentResult, AssessmentScoringAdjustment, AssessmentSettings, AssessmentSource, AssessmentSummary, PublicAssessmentQuestion } from '../assessment';
import { boolean, dictionary, enumeration, integer, invalid, list, nullable, number, optional, questionType, record, text, timestamp, uuid } from './learning-wire';
import { decodeAppeal, gradeHistory } from './live-grading';

function assessmentSource(value: unknown, editor: boolean): AssessmentSource | undefined {
  const row = record(value);
  const documentId = row.documentId ?? row.document_id ?? row.id;
  if (documentId == null || documentId === '') return undefined;
  return {
    ...(editor ? { wire: row } : {}),
    documentId: uuid(documentId), title: text(row.documentTitle ?? row.title ?? ''),
    version: integer(row.version ?? row.versionNumber ?? row.version_number ?? 1, 1),
    locator: text(row.locator ?? ''),
    accessible: row.accessible === true || row.isAccessible === true,
    ...(editor ? {
      documentVersionId: optional(row.document_version_id ?? row.documentVersionId, uuid),
      sourceId: optional(row.source_id ?? row.sourceId, text),
    } : {}),
  };
}
function choice(value: unknown) {
  const row = record(value);
  return { id: text(row.id), text: text(row.text), label: text(row.label) };
}
export function publicQuestion(value: unknown, editor = false): PublicAssessmentQuestion {
  const row = record(value);
  const type = questionType(row.type);
  const source = row.source == null ? undefined : assessmentSource(row.source, editor);
  const base = { id: text(row.id), prompt: text(row.prompt), points: number(row.points), source };
  if (type === 'single_choice' || type === 'multiple_choice') {
    return { ...base, type, options: list(row.options, value => editor ? choice(value) : publicChoice(value)) };
  }
  if (type === 'true_false') {
    return { ...base, type, statements: list(row.statements, value => editor ? choice(value) : publicChoice(value)) };
  }
  return { ...base, type };
}
function publicChoice(value: unknown) {
  const row = record(value);
  return { id: text(row.id), text: text(row.text) };
}
function answerValue(value: unknown): AssessmentAnswer['value'] {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return list(value, text);
  return dictionary(value, boolean);
}
export function answer(value: unknown, expectedId: string): AssessmentAnswer {
  const row = record(value);
  if (row.question_id !== expectedId) return invalid();
  return { questionId: expectedId, value: answerValue(row.value) };
}
export function editorQuestion(value: unknown): AssessmentQuestion {
  const row = record(value);
  return {
    ...publicQuestion(row, true),
    key: row.key === null ? '' : answerValue(row.key),
    explanation: text(row.explanation), rubric: text(row.rubric),
    reviewed: boolean(row.reviewed), aiProposed: boolean(row.ai_proposed),
    manualGradingRequired: boolean(row.manual_grading_required), acceptedAnswers: list(row.accepted_answers, text),
    tolerance: number(row.tolerance), topic: text(row.topic),
  };
}
function dateValue(value: string) {
  if (!value) return null;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) throw new ApiError('VALIDATION_ERROR', 'Thời gian kiểm tra không hợp lệ.', 422);
  return date.toISOString();
}
export function encodeSettings(settings: AssessmentSettings) {
  return {
    title: settings.title, code: settings.code, topic: settings.topic,
    opens_at: dateValue(settings.opensAt), closes_at: dateValue(settings.closesAt),
    duration_minutes: settings.durationMinutes, max_attempts: settings.maxAttempts,
    grading_policy: settings.gradingPolicy, results_at: dateValue(settings.resultsAt), appeal_window_days: settings.appealWindowDays,
  };
}
export function encodeQuestion(question: AssessmentQuestion) {
  return {
    id: question.id, type: question.type, prompt: question.prompt, points: question.points,
    key: question.key === '' ? null : question.key,
    options: question.type === 'single_choice' || question.type === 'multiple_choice' ? question.options : [],
    statements: question.type === 'true_false' ? question.statements : [],
    explanation: question.explanation, rubric: question.rubric, reviewed: question.reviewed,
    ai_proposed: question.aiProposed, manual_grading_required: question.manualGradingRequired,
    accepted_answers: question.acceptedAnswers, tolerance: question.tolerance, topic: question.topic,
    source: question.source?.wire ?? question.source ?? null,
  };
}

export function decodeScoringAdjustment(value: unknown): AssessmentScoringAdjustment {
  const row = record(value);
  return {
    id: uuid(row.id),
    versionId: uuid(row.version_id ?? row.activity_version_id),
    classId: uuid(row.class_id),
    questionId: text(row.question_id),
    action: enumeration(row.action, 'VOID_QUESTION', 'GRANT_FULL_CREDIT', 'ACCEPT_ADDITIONAL_KEY'),
    status: enumeration(row.status, 'PROPOSED', 'APPROVED', 'REJECTED'),
    payload: row.payload == null ? {} : record(row.payload),
    reason: text(row.reason),
    decisionReason: nullable(row.decision_reason, text),
    proposedBy: optional(row.proposed_by, uuid),
    createdAt: optional(row.created_at, timestamp),
    decidedAt: nullable(row.decided_at, timestamp),
    regradedCount: optional(row.regraded_count, value => integer(value, 0)),
  };
}

function settings(row: Record<string, unknown>): AssessmentSettings {
  return {
    title: text(row.title), code: text(row.code), topic: text(row.topic),
    opensAt: timestamp(row.opens_at), closesAt: timestamp(row.closes_at), durationMinutes: integer(row.duration_minutes, 1),
    maxAttempts: integer(row.max_attempts, 1), gradingPolicy: enumeration(row.grading_policy, 'BEST', 'LATEST', 'AVERAGE'),
    resultsAt: nullable(row.results_at, timestamp) ?? '',
    appealWindowDays: optional(row.appeal_window_days, value => integer(value, 1)),
  };
}
export function decodeDraft(value: unknown): AssessmentDraft {
  const row = record(value);
  const config = record(row.settings);
  const revision = integer(row.revision, 1);
  const status = enumeration(row.status, 'DRAFT', 'PENDING_APPROVAL', 'PUBLISHED');
  return {
    classId: uuid(row.class_id), assessmentId: uuid(row.assessment_id), revision, status,
    versionId: optional(row.version_id, uuid),
    versionNumber: integer(row.version_number, 1),
    settings: Object.keys(config).length === 0 && status === 'DRAFT'
      ? { title: '', code: '', opensAt: '', closesAt: '', durationMinutes: 0, maxAttempts: 1, gradingPolicy: 'BEST', resultsAt: '' }
      : settings(config),
    questions: list(row.questions, editorQuestion),
    rejectionReason: nullable(row.rejection_reason, text),
    authorName: '',
    availableSources: list(row.available_sources ?? [], value => assessmentSource(value, true) ?? invalid()),
    canApprove: optional(row.can_approve, boolean) ?? false,
    attemptCount: optional(row.attempt_count, value => integer(value, 0)),
    canCreateVersion: optional(row.can_create_version, boolean),
    scoringAdjustments: row.scoring_adjustments === undefined ? [] : list(row.scoring_adjustments, decodeScoringAdjustment),
  };
}
export function decodeSummary(value: unknown): AssessmentSummary {
  const row = record(value);
  const status = enumeration(row.status, 'UPCOMING', 'OPEN', 'CLOSED');
  const canStart = boolean(row.can_start);
  const activeAttemptId = nullable(row.active_attempt_id, uuid);
  return {
    ...settings(row), id: uuid(row.id), versionNumber: integer(row.version_number, 1),
    questionCount: integer(row.question_count), totalPoints: number(row.total_points), status,
    usedAttempts: integer(row.used_attempts), activeAttemptId,
    completedAttemptId: nullable(row.completed_attempt_id, uuid), canStart,
    unavailableReason: canStart || activeAttemptId ? null : 'Hiện chưa thể bắt đầu lượt làm mới.',
  };
}
export function receipt(value: unknown): AssessmentReceipt {
  const row = record(value);
  return {
    id: uuid(row.id), classId: uuid(row.class_id), assessmentId: uuid(row.assessment_id),
    attemptId: uuid(row.attempt_id), versionId: uuid(row.version_id), versionNumber: integer(row.version_number, 1),
    reason: enumeration(row.reason, 'MANUAL', 'TIMEOUT'), submittedAt: timestamp(row.submitted_at),
    savedAnswerCount: integer(row.saved_answer_count), totalQuestionCount: integer(row.total_question_count),
    savedRevision: integer(row.saved_revision), resultReleased: boolean(row.result_released),
  };
}
export function decodeAttempt(value: unknown): AssessmentAttempt {
  const row = record(value);
  const status = enumeration(row.status, 'IN_PROGRESS', 'SUBMITTED', 'AUTO_SUBMITTED', 'GRADED', 'RELEASED');
  const confirmation = nullable(row.receipt, receipt);
  if ((status === 'IN_PROGRESS') !== (confirmation === null)) return invalid();
  return {
    classId: uuid(row.class_id), assessmentId: uuid(row.assessment_id), attemptId: uuid(row.attempt_id),
    ownerId: uuid(row.owner_id), title: text(row.title), code: text(row.code), className: '',
    versionId: uuid(row.version_id), versionNumber: integer(row.version_number, 1), attemptNumber: integer(row.attempt_number, 1),
    questions: list(row.questions, publicQuestion), savedAnswers: dictionary(row.saved_answers, answer),
    savedRevision: integer(row.saved_revision), savedAt: timestamp(row.saved_at), serverNow: timestamp(row.server_now),
    startedAt: timestamp(row.started_at), endsAt: timestamp(row.ends_at),
    status: status === 'IN_PROGRESS' ? status : 'SUBMITTED', receipt: confirmation,
  };
}
export function decodeResult(value: unknown): AssessmentResult {
  const row = record(value);
  const released = boolean(row.released);
  const attemptId = uuid(row.attempt_id), classId = uuid(row.class_id);
  const confirmation = receipt(row.receipt);
  const assessmentId = uuid(row.assessment_id);
  if (confirmation.attemptId !== attemptId || confirmation.classId !== classId || confirmation.assessmentId !== assessmentId) return invalid();
  const appeals = released ? list(row.appeals, decodeAppeal) : [];
  if (appeals.some(appeal => appeal.targetType !== 'ASSESSMENT_ATTEMPT' || appeal.submissionId !== attemptId || appeal.classId !== classId)) return invalid();
  const current = appeals.find(appeal => appeal.status !== 'RESOLVED') ?? appeals[0];
  const allowed = released && boolean(row.can_appeal);
  return {
    classId, assessmentId, attemptId, released, title: text(row.title), versionNumber: integer(row.version_number, 1),
    receipt: { ...confirmation, resultReleased: released },
    effectiveGrade: released ? nullable(row.effective_grade, gradeHistory) : null,
    questions: released ? list(row.questions, value => {
      const entry = record(value);
      const question = publicQuestion(entry.question);
      return {
        question, answer: nullable(entry.answer, value => answer(value, question.id)),
        score: nullable(entry.score, number), maximum: number(entry.maximum),
        releasedSolution: text(entry.released_solution), source: question.source ?? null, feedback: '',
      };
    }) : [],
    gradeHistory: released ? list(row.grade_history, gradeHistory) : [],
    appeal: {
      allowed: allowed && current?.status !== 'OPEN' && current?.status !== 'PROPOSED', reason: allowed ? null : released ? 'Hiện không thể gửi yêu cầu xem xét.' : 'Kết quả chưa được công bố.',
      status: current ? current.status === 'RESOLVED' ? 'RESOLVED' : 'PENDING' : 'NONE',
      submittedReason: current?.reason ?? null,
    },
    pendingGradeProposal: appeals.some(appeal => appeal.status === 'PROPOSED'),
  };
}
