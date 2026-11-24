import { routeHref } from '@/config/routes';
import { ApiError } from '../api/errors';
import { assessmentAttemptHref, type AssessmentAttemptTarget, type AssessmentDraft, type AssessmentPort, type AssessmentSubmission, type OfficialCorrectionTarget } from '../assessment';
import { answer, decodeAttempt, decodeDraft, decodeResult, decodeScoringAdjustment, decodeSummary, editorQuestion, encodeQuestion, encodeSettings, receipt } from './assessment-wire';
import { quizSourceUnavailable, type LiveQuizGenerator } from './live-ai';
import type { LiveIdentitySession } from './live-identity';
import { createTeacherActivityReader } from './live-learning';
import { createGradingReader, decodeAppeal, decodeProposal, findQueueRow, gradeHistory } from './live-grading';
import { boolean, enumeration, integer, invalid, learningReader, list, nullable, number, optional, readHistoryClass, record, requireLearningSession, text, timestamp, unavailable, unavailableMessage, uuid, type LearningCore } from './learning-wire';

function inScope<T extends AssessmentAttemptTarget>(value: T, target: AssessmentAttemptTarget): T {
  if (value.classId !== target.classId || value.assessmentId !== target.assessmentId || value.attemptId !== target.attemptId) return invalid();
  return value;
}

function confirmation(value: unknown, target: AssessmentAttemptTarget): Extract<AssessmentSubmission, { state: 'CONFIRMED' }> {
  const row = record(value);
  return { state: enumeration(row.state, 'CONFIRMED'), receipt: inScope(receipt(row.receipt), target) };
}

function decodeAssessmentGrading(value: unknown, input: AssessmentAttemptTarget) {
  const row = record(value);
  const target = inScope({ classId: uuid(row.class_id), assessmentId: uuid(row.assessment_id), attemptId: uuid(row.attempt_id) }, input);
  const gradeId = nullable(row.grade_id, uuid);
  const proposal = nullable(row.pending_proposal, decodeProposal);
  if (proposal && (proposal.classId !== input.classId || proposal.submissionId !== input.attemptId ||
    proposal.targetType !== 'ASSESSMENT_ATTEMPT' || proposal.gradeId !== gradeId || proposal.status !== 'PENDING')) return invalid();
  return {
    ...target, gradeId, studentId: uuid(row.student_id), studentName: text(row.student_name),
    title: text(row.title), versionNumber: integer(row.version_number, 1), released: boolean(row.released),
    gradeVersion: integer(row.grade_version), effectiveGrade: nullable(row.effective_grade, gradeHistory),
    gradeHistory: list(row.grade_history, gradeHistory), canApprove: boolean(row.can_approve),
    appealId: nullable(row.appeal_id, uuid), appealReason: nullable(row.appeal_reason, text),
    questions: list(row.questions, value => {
      const entry = record(value);
      const question = editorQuestion(entry.question);
      return {
        question, answer: nullable(entry.answer, value => answer(value, question.id)),
        score: nullable(entry.score, number), feedback: text(entry.feedback),
        maximum: optional(entry.maximum, number) ?? question.points,
        canGrade: optional(entry.can_grade, boolean) ?? false,
      };
    }),
    pendingProposal: proposal ? {
      id: proposal.id, baseVersion: proposal.expectedGradeVersion,
      scores: Object.fromEntries(Object.entries(proposal.scores ?? {}).filter((entry): entry is [string, number] => entry[1] !== null)),
      feedback: proposal.feedback, reason: proposal.reason, proposedBy: proposal.proposerId,
    } : null,
  };
}

export function createAssessmentGradingReader(session: LiveIdentitySession) {
  const read = learningReader(session);
  return (input: AssessmentAttemptTarget, signal?: AbortSignal) =>
    read('assessmentGrading', { pathParams: input, signal }, value => decodeAssessmentGrading(value, input));
}

function correctionTarget(
  target: Omit<OfficialCorrectionTarget, 'effectiveScore'> & { released: boolean; effectiveScore: number | null },
): OfficialCorrectionTarget {
  const { released, effectiveScore, ...item } = target;
  if (!released || effectiveScore === null) {
    throw new ApiError('CONFLICT', 'Kết quả chưa được công bố để điều chỉnh.', 409);
  }
  const { classId, assessmentId, attemptId, title, studentId, studentName, gradeHistory, gradeVersion, feedback, appealId, appealReason } = item;
  return { classId, assessmentId, attemptId, title, studentId, studentName, gradeHistory, gradeVersion, effectiveScore, feedback, appealId, appealReason };
}

export function createLiveAssessment(session: LiveIdentitySession, core: LearningCore, ai: LiveQuizGenerator): AssessmentPort {
  const read = learningReader(session);
  const activities = createTeacherActivityReader(session);
  const grading = createGradingReader(session);
  const readGrading = createAssessmentGradingReader(session);
  let cachedDraft: { generation: number; draft: AssessmentDraft } | undefined;
  async function refreshGrading(input: AssessmentAttemptTarget, generation: number) {
    try {
      requireLearningSession(session, generation);
      return await readGrading(input);
    } catch (error) {
      if (error instanceof ApiError) error.outcome = 'unknown';
      throw error;
    }
  }
  const readResult = (input: AssessmentAttemptTarget, signal?: AbortSignal) =>
    read('assessmentResult', { pathParams: input, signal }, value => inScope(decodeResult(value), input));
  const getDraft = (value: unknown, input: { classId: string; assessmentId: string }): AssessmentDraft => {
    const draft = decodeDraft(value);
    return draft.classId === input.classId && draft.assessmentId === input.assessmentId ? draft : invalid();
  };
  async function rememberDraft(load: () => Promise<AssessmentDraft>) {
    const generation = session.sessionGeneration();
    const draft = await load();
    requireLearningSession(session, generation);
    cachedDraft = { generation, draft };
    return draft;
  }
  const readDraft = (input: { classId: string; assessmentId: string }, signal?: AbortSignal) =>
    rememberDraft(() => read('assessmentDraft', { pathParams: input, signal }, value => getDraft(value, input)));

  return {
    readOwnHistory: resultId => read('historyResult', { pathParams: { resultId } }, value => {
      const row = record(value);
      if ('submission_id' in row) throw new ApiError('NOT_FOUND', 'Không tìm thấy kết quả kiểm tra.', 404);
      const result = decodeResult(row);
      return result.attemptId === resultId ? result : invalid();
    }),
    async load(routeId, params, signal) {
      const input = { classId: params.classId, assessmentId: params.assessmentId, attemptId: params.attemptId };
      const { classId, assessmentId } = input;
      try {
        if (routeId === 's.assessments' || routeId === 's.assessment') {
          const isList = routeId === 's.assessments';
          const [classSummary, row] = await Promise.all([
            core.readClass(classId, signal), read(isList ? 'assessments' : 'assessment', { pathParams: input, signal }, record),
          ]);
          const serverNow = timestamp(row.server_now);
          if (isList) return { kind: 'ready', classSummary, page: { type: 'list', classSummary, serverNow, items: list(row.items, decodeSummary) } };
          const assessment = decodeSummary(row.assessment);
          if (assessment.id !== assessmentId) return invalid();
          return { kind: 'ready', classSummary, page: { type: 'detail', classSummary, assessment, serverNow } };
        }
        if (routeId === 's.examResult' || routeId === 's.receipt') {
          const [result, classSummary] = await Promise.all([
            readResult(input, signal), readHistoryClass(core, classId, signal),
          ]);
          const page = routeId === 's.examResult' ? { type: 'result' as const, result }
            : { type: 'receipt' as const, receipt: result.receipt, title: result.title };
          return { kind: 'ready', classSummary, page };
        }
        if (routeId === 's.examAttempt') {
          const [attempt, classSummary] = await Promise.all([
            read('assessmentAttempt', { pathParams: input, signal }, value => inScope(decodeAttempt(value), input)),
            readHistoryClass(core, classId, signal),
          ]);
          return { kind: 'ready', classSummary, page: { type: 'attempt', attempt: { ...attempt, className: classSummary?.name ?? '' } } };
        }
        if (routeId === 't.assessmentEditor' || routeId === 't.assessmentApproval') {
          const [classSummary, draft] = await Promise.all([
            core.readClass(classId, signal), readDraft(input, signal),
          ]);
          const type = routeId === 't.assessmentApproval' ? 'approval' : 'editor';
          return { kind: 'ready', classSummary, page: { type, classSummary, draft } };
        }
        if (routeId === 't.examGrading') {
          const [classSummary, detail] = await Promise.all([core.readClass(classId, signal), readGrading(input, signal)]);
          return { kind: 'ready', classSummary, page: { type: 'grading', grading: detail } };
        }
        throw new ApiError('NOT_FOUND', 'Không tìm thấy trang kiểm tra.', 404);
      } catch (error) {
        if (error instanceof ApiError && error.status === 503) return { kind: 'unavailable', message: unavailableMessage };
        throw error;
      }
    },
    listCalendarEvents: classId => core.readEvents(classId),
    listTeacherActivities: classId => activities(classId, 'ASSESSMENT'),
    createDraft: classId => read('createAssessment', { pathParams: { classId } }, value => {
      const assessmentId = uuid(record(value).assessment_id);
      return { assessmentId, href: routeHref('t.assessmentEditor', { classId, assessmentId }) ?? invalid() };
    }),
    saveDraft: input => rememberDraft(() => read('saveAssessmentDraft', {
      pathParams: input, body: { expected_revision: input.expectedRevision, settings: encodeSettings(input.settings), questions: input.questions.map(encodeQuestion) },
    }, value => getDraft(value, input))),
    reviewQuestion: input => rememberDraft(() => read('reviewAssessmentQuestion', {
      pathParams: input, body: { expected_revision: input.expectedRevision },
    }, value => getDraft(value, input))),
    propose: input => rememberDraft(() => read('proposeAssessment', {
      pathParams: input, body: { expected_revision: input.expectedRevision },
    }, value => getDraft(value, input))),
    decide: input => rememberDraft(() => read('decideAssessment', {
      pathParams: input, body: { expected_revision: input.expectedRevision, decision: input.decision, reason: input.reason },
    }, value => getDraft(value, input))),
    newVersion: input => rememberDraft(() => read('newAssessmentVersion', { pathParams: input }, value => getDraft(value, input))),
    async generateQuestions(input) {
      const generation = session.sessionGeneration();
      const current = cachedDraft?.generation === generation ? cachedDraft.draft : undefined;
      const draft = current?.classId === input.classId && current.assessmentId === input.assessmentId
        ? current : await readDraft(input);
      const source = draft.availableSources.find(item => item.accessible &&
        item.documentId === input.documentId && item.version === input.version);
      if (!source?.documentVersionId) throw new ApiError('UNAVAILABLE', quizSourceUnavailable, 503);
      requireLearningSession(session, generation);
      const items = await ai.generateQuizDrafts({
        classId: input.classId, documentVersionId: source.documentVersionId, purpose: 'ASSESSMENT_DRAFT', type: input.type, count: 2,
      });
      return items.map(item => item.assessmentQuestion(source));
    },
    start: input => read('startAssessment', { pathParams: input }, value => {
      const attemptId = uuid(record(value).attempt_id);
      return { attemptId, href: assessmentAttemptHref({ classId: input.classId, assessmentId: input.assessmentId, attemptId }) };
    }),
    saveAnswers: input => read('saveAssessmentAnswers', {
      pathParams: input,
      body: { expected_revision: input.expectedRevision, answers: Object.fromEntries(Object.entries(input.answers).map(
        ([id, answer]) => [id, { question_id: answer.questionId, value: answer.value }],
      )) },
    }, value => inScope(decodeAttempt(value), input)),
    async submit(input) {
      try {
        return await read('submitAssessment', {
          pathParams: input, headers: { 'Idempotency-Key': input.operationId },
          body: { operation_id: input.operationId, expected_revision: input.expectedRevision },
        }, value => confirmation(value, input));
      } catch (error) {
        if (error instanceof ApiError && error.outcome === 'unknown' && error.code !== 'SESSION_CHANGED') {
          return { state: 'UNKNOWN', operationId: input.operationId };
        }
        throw error;
      }
    },
    reconcileSubmission: input => read('reconcileAssessment', {
      pathParams: input, query: { operation_id: input.operationId },
    }, value => {
      const row = record(value);
      const state = enumeration(row.state, 'IN_PROGRESS', 'CONFIRMED');
      return state === 'CONFIRMED' ? confirmation(value, input)
        : { state, attempt: inScope(decodeAttempt(row.attempt), input) };
    }),
    async appeal(input) {
      const generation = session.sessionGeneration();
      await read('createAppeal', {
        pathParams: input, body: { target_type: 'ASSESSMENT_ATTEMPT', target_id: input.attemptId, question_id: input.questionId, reason: input.reason },
      }, value => {
        const appeal = decodeAppeal(value);
        if (appeal.targetType !== 'ASSESSMENT_ATTEMPT' || appeal.submissionId !== input.attemptId || appeal.classId !== input.classId) return invalid();
      });
      try {
        requireLearningSession(session, generation);
        return await readResult(input);
      }
      catch (error) {
        if (error instanceof ApiError) error.outcome = 'unknown';
        throw error;
      }
    },
    gradeAttempt: input =>
      read('gradeAssessment', {
        pathParams: input, body: { expected_grade_version: input.expectedGradeVersion, scores: input.scores, feedback: input.feedback },
      }, value => decodeAssessmentGrading(value, input)),
    releaseResults: input => read('releaseAssessment', {
      pathParams: input, body: { reason: input.reason, release_now: input.releaseNow ?? true },
    }, value => ({ releasedCount: integer(record(value).released_count) })),
    async proposeGrade(input) {
      const generation = session.sessionGeneration();
      const { items } = await grading(input.classId);
      const item = findQueueRow(items, 'ASSESSMENT', input.assessmentId, input.attemptId);
      if (!item.gradeId) throw new ApiError('NOT_FOUND', 'Không tìm thấy điểm kiểm tra.', 404);
      requireLearningSession(session, generation);
      await read('proposeGradeRevision', {
        pathParams: { gradeId: item.gradeId }, body: {
          expected_grade_version: input.expectedGradeVersion, scores: input.scores,
          feedback: input.feedback, reason: input.reason,
        },
      }, value => {
        const proposal = decodeProposal(value);
        if (proposal.gradeId !== item.gradeId || proposal.targetType !== 'ASSESSMENT_ATTEMPT' ||
          proposal.submissionId !== item.id || proposal.status !== 'PENDING') return invalid();
      });
      return refreshGrading(input, generation);
    },
    async approveGrade(input) {
      const generation = session.sessionGeneration();
      const { items, proposals } = await grading(input.classId);
      const item = findQueueRow(items, 'ASSESSMENT', input.assessmentId, input.attemptId);
      const pending = proposals.find(row => row.id === input.proposalId && row.submissionId === item.id && row.gradeId === item.gradeId);
      if (!pending) throw new ApiError('NOT_FOUND', 'Không tìm thấy đề xuất điểm cho lượt làm này.', 404);
      requireLearningSession(session, generation);
      await read('decideGradeRevision', {
        pathParams: { revisionId: input.proposalId },
        body: { expected_grade_version: pending.expectedGradeVersion, decision: 'APPROVE', reason: input.reason },
      }, value => {
        const proposal = decodeProposal(value);
        if (proposal.id !== pending.id || proposal.gradeId !== pending.gradeId || proposal.status !== 'APPROVED') return invalid();
      });
      return refreshGrading(input, generation);
    },
    async listSubmittedAttempts(classId) {
      const { items } = await grading(classId);
      return items.filter(item => item.kind === 'ASSESSMENT').map(item => ({
        classId, assessmentId: item.activityId, attemptId: item.id, title: item.title,
        studentName: item.studentName, submittedAt: item.submittedAt,
        released: item.released, effectiveScore: item.effectiveScore, href: item.href,
      }));
    },
    async listGrading(classId) {
      const { items } = await grading(classId);
      return items.filter(item => item.kind === 'ASSESSMENT' && item.released)
        .map(item => correctionTarget({ ...item, assessmentId: item.activityId, attemptId: item.id }));
    },
    async getCorrectionTarget(input) {
      const item = await readGrading(input);
      return correctionTarget({ ...item, effectiveScore: item.effectiveGrade?.score ?? null, feedback: item.effectiveGrade?.feedback ?? '' });
    },
    async applyGradeCorrection() {
      return unavailable('Điểm kiểm tra đã công bố phải được điều chỉnh qua đề xuất và phê duyệt.');
    },
    proposeScoringAdjustment: input => read('proposeScoringAdjustment', {
      pathParams: { classId: input.classId, versionId: input.versionId },
      body: { question_id: input.questionId, action: input.action, payload: input.payload ?? {}, reason: input.reason },
    }, decodeScoringAdjustment),
    decideScoringAdjustment: input => read('decideScoringAdjustment', {
      pathParams: { adjustmentId: input.adjustmentId },
      body: { decision: input.decision, reason: input.reason },
    }, decodeScoringAdjustment),
  };
}
