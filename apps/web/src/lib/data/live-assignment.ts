import { routeHref } from '@/config/routes';
import { ApiError } from '../api/errors';
import type { AssignmentDefinition, AssignmentDraftInput, AssignmentGrade, AssignmentPolicy, AssignmentPort, AssignmentSource, AssignmentSubmission, SubmissionFile } from '../assignment';
import { decodeResult } from './assessment-wire';
import { boolean, enumeration, integer, invalid, learningReader, list, nullable, number, optional, readHistoryClass, record, requireLearningSession, text, timestamp, uuid, type LearningCore } from './learning-wire';
import { createAssessmentGradingReader } from './live-assessment';
import { createGradingReader, decodeAppeal, decodeGrade, decodeProposal, appealModel, proposalModel, type QueueRow } from './live-grading';
import type { LiveIdentitySession } from './live-identity';
import { createTeacherActivityReader } from './live-learning';

type Scope = { classId: string; assignmentId: string };

function source(value: unknown, teacher: boolean): AssignmentSource | undefined {
  const row = record(value);
  const accessible = row.isAccessible === true || row.accessible === true;
  if (!teacher && !accessible) return undefined;
  return {
    ...row,
    id: uuid(row.id ?? row.document_id ?? row.documentId),
    title: text(row.title ?? row.documentTitle ?? ''),
    version: integer(row.version ?? row.versionNumber ?? row.version_number ?? 1, 1),
    accessible: teacher || accessible,
  };
}

function policy(value: unknown): AssignmentPolicy {
  const row = record(value);
  return {
    maxVersions: integer(row.max_versions, 1), allowLate: boolean(row.allow_late),
    lateUntil: nullable(row.late_until, timestamp), resubmitUntil: nullable(row.resubmit_until, timestamp),
    gradedVersion: enumeration(row.graded_version, 'FIRST', 'LATEST'),
    maxBytes: nullable(row.max_bytes, value => integer(value, 1)), acceptedTypes: list(row.accepted_types, text),
    newEnrollment: enumeration(row.new_enrollment, 'AUTO', 'REVIEW'),
    maxScore: number(row.max_score), appealWindowDays: optional(row.appeal_window_days, value => integer(value, 1)),
  };
}

function definition(value: unknown, teacher: boolean, historical = false): AssignmentDefinition {
  const row = record(value);
  const wireStatus = historical ? enumeration(row.status, 'PUBLISHED', 'SUPERSEDED', 'CLOSED')
    : enumeration(row.status, 'DRAFT', 'PUBLISHED');
  const status = wireStatus === 'DRAFT' ? 'DRAFT' : 'PUBLISHED';
  const revision = integer(row.revision, 1);
  const id = uuid(row.assignment_id);
  if (id !== row.id) return invalid();
  const classId = uuid(row.class_id);
  if (status === 'DRAFT' && !('title' in row) && teacher) {
    return {
      id, classId, status, revision, title: '', topic: '', instructions: '', dueAt: '',
      policy: {
        maxVersions: 1, allowLate: false, lateUntil: null, resubmitUntil: null,
        gradedVersion: 'LATEST', maxBytes: null, acceptedTypes: [], newEnrollment: 'AUTO',
      },
    };
  }
  return {
    id, classId, status, revision, title: text(row.title), topic: text(row.topic),
    instructions: text(row.instructions), dueAt: timestamp(row.due_at), policy: policy(row.policy),
    source: row.source == null ? undefined : source(row.source, teacher),
    publishedRevision: status === 'PUBLISHED' ? revision : undefined,
    appealWindowDays: optional(row.appeal_window_days, value => integer(value, 1)),
  };
}

export function decodeAssignmentSummary(value: unknown): AssignmentDefinition {
  return definition(value, false);
}

function toTimestamp(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) throw new ApiError('VALIDATION_ERROR', 'Thời hạn bài tập không hợp lệ.', 422);
  return date.toISOString();
}

function encodeDraft(input: AssignmentDraftInput) {
  const p = input.policy;
  return {
    expected_revision: input.expectedRevision, title: input.title, topic: input.topic, instructions: input.instructions,
    due_at: toTimestamp(input.dueAt), source: input.source ?? null, publish: input.publish, appeal_window_days: input.appealWindowDays,
    policy: {
      max_versions: p.maxVersions, allow_late: p.allowLate, late_until: toTimestamp(p.lateUntil),
      resubmit_until: toTimestamp(p.resubmitUntil), graded_version: p.gradedVersion, max_bytes: p.maxBytes,
      accepted_types: p.acceptedTypes, new_enrollment: p.newEnrollment, max_score: p.maxScore, appeal_window_days: p.appealWindowDays,
    },
  };
}

function file(value: unknown): SubmissionFile {
  const row = record(value);
  return { id: uuid(row.id), name: text(row.name), type: text(row.type), size: integer(row.size) };
}

function assignmentGrade(value: unknown): AssignmentGrade {
  const grade = decodeGrade(value);
  if (grade.confirmedAt === null) return invalid();
  return {
    version: grade.version, score: grade.score, maxScore: grade.maxScore, feedback: grade.feedback,
    teacherId: grade.teacherId, teacherName: grade.teacherName, confirmedAt: grade.confirmedAt, reason: grade.reason,
  };
}

function submission(value: unknown): AssignmentSubmission {
  const row = record(value);
  const id = uuid(row.submission_id);
  if (id !== row.id) return invalid();
  return {
    id, classId: uuid(row.class_id), assignmentId: uuid(row.assignment_id), assignmentRevision: integer(row.assignment_revision, 1),
    resultId: uuid(row.result_id), studentId: uuid(row.student_id), studentName: text(row.student_name), number: integer(row.number, 1),
    file: file(row.file), note: text(row.note), submittedAt: timestamp(row.submitted_at), late: boolean(row.is_late),
    isGradedVersion: boolean(row.is_graded_version), grade: nullable(row.grade, assignmentGrade), gradeHistory: list(row.grade_history, assignmentGrade),
  };
}
function inScope<T extends { classId: string }>(item: T, itemId: string, input: Scope): T {
  if (item.classId !== input.classId || itemId !== input.assignmentId) return invalid();
  return item;
}

function attachmentName(headers: Headers) {
  const value = headers.get('Content-Disposition') ?? '';
  const encoded = /(?:^|;)\s*filename\*=UTF-8''([^;]+)/i.exec(value)?.[1];
  if (encoded) {
    try {
      const name = decodeURIComponent(encoded).replace(/[/\\\u0000-\u001f\u007f]/g, '_');
      if (name && name !== '.' && name !== '..') return name;
    } catch { /* Invalid filename encoding. Use the generic name. */ }
  }
  return 'submission';
}

const submitReasons: Record<string, string> = {
  NOT_IN_AUDIENCE: 'Bài tập này không được giao cho tài khoản của bạn.',
  SUBMISSION_LIMIT_REACHED: 'Bạn đã sử dụng hết số lượt nộp bài.',
  LATE_SUBMISSION_NOT_ALLOWED: 'Bài tập đã quá hạn nộp.',
  RESUBMISSION_CLOSED: 'Đã quá thời hạn nộp lại bài tập.',
};

export function createLiveAssignment(session: LiveIdentitySession, core: LearningCore): AssignmentPort {
  const read = learningReader(session);
  const activities = createTeacherActivityReader(session);
  const grading = createGradingReader(session);
  const assessmentGrading = createAssessmentGradingReader(session);
  const history = (resultId: string, signal?: AbortSignal) => read('historyResult', {
    pathParams: { resultId }, signal,
  }, value => {
    const row = record(value);
    const limitedHistory = boolean(row.limited_history);
    if (!('submission_id' in row)) {
      const result = decodeResult(row);
      if (result.attemptId !== resultId) return invalid();
      return { kind: 'official-history' as const, result, limitedHistory };
    }
    const item = submission(row);
    if (item.resultId !== resultId) return invalid();
    const appeals = row.appeals === undefined ? [] : list(row.appeals, decodeAppeal);
    if (appeals.some(appeal => appeal.targetType !== 'ASSIGNMENT_SUBMISSION' ||
      appeal.submissionId !== item.id || appeal.classId !== item.classId)) return invalid();
    return {
      kind: 'history' as const, submission: item, limitedHistory,
      appeals: appeals.map(appeal => appealModel(appeal)), proposals: [],
      canAppeal: row.can_appeal === undefined ? false : boolean(row.can_appeal),
    };
  });

  const detail = (input: Scope, teacher: boolean, signal?: AbortSignal) =>
    read('assignment', { pathParams: input, signal }, value => {
      const row = record(value);
      const item = definition(row.assignment, teacher);
      return {
        assignment: inScope(item, item.id, input),
        submissions: list(row.submissions, value => {
          const item = submission(value);
          return inScope(item, item.assignmentId, input);
        }),
        canSubmit: boolean(row.can_submit),
        blocked: optional(row.submit_blocked_reason, text),
      };
    });
  const match = (items: QueueRow[], id: string, kind: QueueRow['kind']) => items.find(row => row.id === id && row.kind === kind);

  const assignmentGrading = (input: Scope & { submissionId: string }, signal?: AbortSignal) =>
    read('assignmentGrading', { pathParams: input, signal }, value => {
      const row = record(value);
      const assignment = definition(row.assignment, true, true);
      const selected = submission(row.submission);
      if (selected.id !== input.submissionId) return invalid();
      inScope(selected, selected.assignmentId, input);
      const submissions = list(row.submissions, value => {
        const item = submission(value);
        if (item.studentId !== selected.studentId) return invalid();
        return inScope(item, item.assignmentId, input);
      });
      if (!submissions.some(item => item.id === selected.id)) return invalid();
      const appeals = list(row.appeals, decodeAppeal);
      if (appeals.some(appeal => appeal.targetType !== 'ASSIGNMENT_SUBMISSION' ||
        appeal.classId !== input.classId || appeal.studentId !== selected.studentId ||
        !submissions.some(item => item.id === appeal.submissionId))) return invalid();
      return {
        assignment: inScope(assignment, assignment.id, input), submission: selected, submissions,
        teachingRole: enumeration(row.teaching_role, 'PRIMARY', 'ASSISTING'), appeals: appeals.map(appeal => appealModel(appeal)),
      };
    });
  const recipients = (input: Scope, signal?: AbortSignal) => read('readAssignmentRecipients', { pathParams: input, signal }, value => {
    const row = record(value);
    if (row.class_id !== input.classId || row.assignment_id !== input.assignmentId) return invalid();
    return {
      revision: integer(row.revision, 1), defaultAssigned: optional(row.default_assigned, boolean) ?? false,
      audience: list(row.audience, value => {
        const entry = record(value);
        return { userId: uuid(entry.user_id), assigned: boolean(entry.assigned) };
      }),
    };
  });
  async function currentRoster(classId: string, signal?: AbortSignal) {
    try {
      return await core.readClassMembers?.(classId, signal);
    } catch (error) {
      if (error instanceof ApiError && [403, 404, 503].includes(error.status)) return undefined;
      throw error;
    }
  }

  return {
    listCalendarEvents: classId => core.readEvents(classId),
    listTeacherActivities: classId => activities(classId, 'ASSIGNMENT'),
    setRecipients: input => read('assignmentRecipients', {
      pathParams: input, body: { expected_revision: input.expectedRevision, student_ids: input.studentIds },
    }, () => undefined),
    createDraft: classId => read('createAssignment', { pathParams: { classId } }, value => {
      const assignmentId = uuid(record(value).assignment_id);
      return { assignmentId, href: routeHref('t.assignmentEditor', { classId, assignmentId }) ?? invalid() };
    }),
    async load(routeId, params, signal) {
      const { classId, assignmentId } = params;
      if (routeId === 's.historyResult') {
        const result = await history(params.resultId, signal);
        if (result.kind === 'official-history') {
          const classSummary = await readHistoryClass(core, result.result.classId, signal);
          return { ...result, classSummary };
        }
        const classSummary = await readHistoryClass(core, result.submission.classId, signal);
        // History is independently authorized. Only load the current definition with class access.
        let assignment: AssignmentDefinition | undefined;
        if (classSummary?.status === 'ACTIVE' && classSummary.membershipStatus === 'ACTIVE') {
          try {
            const { assignment: current } = await detail(result.submission, false, signal);
            if (current.revision === result.submission.assignmentRevision) assignment = current;
          }
          catch (error) {
            if (!(error instanceof ApiError) || ![403, 404].includes(error.status)) throw error;
          }
        }
        return { ...result, classSummary, assignment };
      }
      if (routeId === 't.assignmentGrading') {
        const [classSummary, snapshot] = await Promise.all([
          core.readClass(classId, signal), assignmentGrading({ classId, assignmentId, submissionId: params.submissionId }, signal),
        ]);
        return { kind: 'grading', classSummary, ...snapshot };
      }
      if (routeId === 't.gradingQueue' || routeId === 't.correction' || routeId === 't.correctionApproval') {
        const [classSummary, queue] = await Promise.all([core.readClass(classId, signal), grading(classId, signal)]);
        const { items, appeals, proposals } = queue;
        const withTarget = (id: string, target: 'ASSESSMENT_ATTEMPT' | 'ASSIGNMENT_SUBMISSION') =>
          match(items, id, target === 'ASSESSMENT_ATTEMPT' ? 'ASSESSMENT' : 'ASSIGNMENT');
        if (routeId === 't.gradingQueue') {
          return {
            kind: 'queue', classSummary, items,
            appeals: appeals.map(row => appealModel(row, withTarget(row.submissionId, row.targetType))),
            proposals: proposals.map(row => proposalModel(row, withTarget(row.submissionId, row.targetType))),
          };
        }
        const pending = proposals.find(row => routeId === 't.correctionApproval'
          ? row.id === params.proposalId : row.appealId === params.appealId);
        const appeal = appeals.find(row => row.id === (routeId === 't.correctionApproval' ? pending?.appealId : params.appealId));
        const target = pending ?? appeal;
        if (!target || (routeId === 't.correctionApproval' && !pending)) {
          throw new ApiError('NOT_FOUND', 'Không tìm thấy hồ sơ xem xét điểm.', 404);
        }
        const item = withTarget(target.submissionId, target.targetType);
        if (!item || item.gradeId !== target.gradeId) return invalid();
        const evidence = item.kind === 'ASSESSMENT'
          ? { kind: 'assessment' as const, grading: await assessmentGrading({ classId, assessmentId: item.activityId, attemptId: item.id }, signal) }
          : { kind: 'assignment' as const, ...await assignmentGrading({ classId, assignmentId: item.activityId, submissionId: item.id }, signal) };
        const studentId = evidence.kind === 'assessment' ? evidence.grading.studentId : evidence.submission.studentId;
        const gradeVersion = evidence.kind === 'assessment' ? evidence.grading.gradeVersion : evidence.submission.grade?.version;
        if (studentId !== item.studentId || (evidence.kind === 'assessment' && evidence.grading.gradeId !== item.gradeId)) return invalid();
        if (gradeVersion !== item.gradeVersion) throw new ApiError('CONFLICT', 'Điểm đã thay đổi. Vui lòng tải lại hồ sơ.', 409);
        const active = classSummary.status === 'ACTIVE';
        const role = evidence.kind === 'assignment' ? evidence.teachingRole : classSummary.teachingRole;
        const actorId = session.currentUser()?.id;
        const canReview = Boolean(active && role && appeal && !pending && appeal.status !== 'RESOLVED');
        return {
          kind: 'appeal-review', classSummary, item, appeal: appeal ? appealModel(appeal, item) : undefined,
          evidence,
          gradeVersion: item.gradeVersion, proposal: pending ? proposalModel(pending, item) : undefined,
          canDecide: Boolean(active && pending && role === 'PRIMARY' &&
            pending.expectedGradeVersion === gradeVersion && actorId && pending.proposerId !== actorId),
          canPropose: canReview,
          canResolve: canReview,
        };
      }
      if (routeId === 's.assignments') {
        const [classSummary, rows] = await Promise.all([
          core.readClass(classId, signal),
          read('assignments', { pathParams: { classId }, signal }, value => list(record(value).items, entry => definition(entry, false))),
        ]);
        const assignments = await Promise.all(rows.map(row => {
          inScope(row, row.id, { classId, assignmentId: row.id });
          return detail({ classId, assignmentId: row.id }, false, signal);
        }));
        return { kind: 'list', classSummary, assignments };
      }
      if (routeId === 't.assignmentEditor') {
        const input = { classId, assignmentId };
        const [classSummary, snapshot, recipientState, roster] = await Promise.all([
          core.readClass(classId, signal), detail(input, true, signal), recipients(input, signal), currentRoster(classId, signal),
        ]);
        const { assignment } = snapshot;
        if (assignment.revision !== recipientState.revision) {
          throw new ApiError('CONFLICT', 'Danh sách giao bài đã thay đổi. Vui lòng tải lại bài tập.', 409);
        }
        const audience = new Map<string, { userId: string; assigned: boolean; displayName?: string }>();
        for (const member of roster ?? []) {
          const userId = uuid(member.id);
          audience.set(userId, { userId, assigned: recipientState.defaultAssigned, displayName: member.displayName });
        }
        for (const entry of recipientState.audience) {
          audience.set(entry.userId, { ...audience.get(entry.userId), ...entry });
        }
        return {
          kind: 'editor', classSummary, assignment, sources: assignment.source ? [assignment.source] : [],
          audience: [...audience.values()], audienceRosterAvailable: roster !== undefined,
        };
      }
      if (routeId === 's.assignment') {
        const [classSummary, snapshot] = await Promise.all([
          core.readClass(classId, signal), detail({ classId, assignmentId }, false, signal),
        ]);
        const graded = snapshot.submissions.find(row => row.grade && row.isGradedVersion);
        const historyResult = graded ? await history(graded.resultId, signal) : undefined;
        if (historyResult && historyResult.kind !== 'history') return invalid();
        return {
          kind: 'detail', classSummary, assignment: snapshot.assignment, submissions: snapshot.submissions,
          canSubmit: snapshot.canSubmit, submitBlockedReason: snapshot.canSubmit ? undefined :
            submitReasons[snapshot.blocked ?? ''] ?? 'Hiện không thể nộp bài tập này.',
          canAppeal: historyResult?.canAppeal ?? false, appeals: historyResult?.appeals ?? [], proposals: [],
        };
      }
      throw new ApiError('NOT_FOUND', 'Không tìm thấy trang bài tập.', 404);
    },
    upload: input => {
      const body = new FormData();
      body.set('file', input.file);
      body.set('expected_revision', String(input.expectedRevision));
      return read('uploadAssignment', { pathParams: { classId: input.classId, assignmentId: input.assignmentId }, body }, value => {
        const row = record(value), id = uuid(row.upload_id);
        const assignmentId = uuid(row.assignment_id), uploaded = file(row.file);
        if (assignmentId !== input.assignmentId || row.preview_only !== true || uploaded.id !== id) return invalid();
        return { id, assignmentId, file: uploaded, acknowledgedAt: timestamp(row.acknowledged_at), previewOnly: true };
      });
    },
    submit: input => read('submitAssignment', {
      pathParams: input, headers: { 'Idempotency-Key': input.operationId },
      body: { upload_id: input.uploadId, expected_revision: input.expectedRevision, operation_id: input.operationId, note: input.note },
    }, value => {
      const item = submission(value);
      if (record(value).operation_id !== input.operationId || item.file.id !== input.uploadId) return invalid();
      return inScope(item, item.assignmentId, input);
    }),
    reconcile: input => read('reconcileAssignment', { pathParams: input, query: { operation_id: input.operationId } }, value => {
      if (value === null) return null;
      const item = submission(value);
      if (record(value).operation_id !== input.operationId) return invalid();
      return inScope(item, item.assignmentId, input);
    }),
    async readFile(input) {
      let received: Response | undefined;
      const bytes = await read('submissionFile', {
        pathParams: input, onResponse: response => { received = response; },
      }, value => value instanceof Uint8Array ? value : invalid());
      if (!received) return invalid();
      return { name: attachmentName(received.headers), type: 'application/octet-stream', bytes };
    },
    saveDraft: input => read('saveAssignmentDraft', { pathParams: input, body: encodeDraft(input) }, value => {
      const item = definition(value, true);
      return inScope(item, item.id, input);
    }),
    grade: input => read('gradeAssignment', {
      pathParams: { submissionId: input.submissionId },
      body: { score: input.score, feedback: input.feedback, expected_grade_version: input.expectedGradeVersion },
    }, assignmentGrade),
    gradeHint: input => read('assignmentGradeHint', { pathParams: input }, value => {
      const row = record(value);
      return { feedback: text(row.feedback), limitations: text(row.limitations) };
    }),
    async appeal(input) {
      const generation = session.sessionGeneration();
      let classId = input.classId;
      if (classId === undefined) {
        const result = await history(input.submissionId);
        if (result.kind !== 'history') throw new ApiError('NOT_FOUND', 'Không tìm thấy bài đã nộp.', 404);
        classId = result.submission.classId;
      }
      requireLearningSession(session, generation);
      return read('createAppeal', {
        pathParams: { classId }, body: { target_type: 'ASSIGNMENT_SUBMISSION', target_id: input.submissionId, reason: input.reason },
      }, value => {
        const appeal = decodeAppeal(value);
        if (appeal.classId !== classId || appeal.submissionId !== input.submissionId || appeal.targetType !== 'ASSIGNMENT_SUBMISSION') return invalid();
        return appealModel(appeal);
      });
    },
    async propose(input) {
      const generation = session.sessionGeneration();
      const queue = await grading(input.classId);
      const appeal = queue.appeals.find(row => row.id === input.appealId);
      if (!appeal) throw new ApiError('NOT_FOUND', 'Không tìm thấy yêu cầu xem xét.', 404);
      const item = match(queue.items, appeal.submissionId, appeal.targetType === 'ASSESSMENT_ATTEMPT' ? 'ASSESSMENT' : 'ASSIGNMENT');
      if (!item || appeal.gradeId !== item.gradeId) return invalid();
      requireLearningSession(session, generation);
      return read('proposeGradeRevision', {
        pathParams: { gradeId: appeal.gradeId },
        body: { expected_grade_version: input.expectedGradeVersion, total_score: input.score, feedback: input.reason, reason: input.reason, appeal_id: input.appealId },
      }, value => {
        const proposal = decodeProposal(value);
        if (proposal.gradeId !== appeal.gradeId || proposal.appealId !== appeal.id || proposal.submissionId !== item.id || proposal.status !== 'PENDING') return invalid();
        return proposalModel(proposal, item);
      });
    },
    decide: input => read('decideGradeRevision', {
      pathParams: { revisionId: input.proposalId },
      body: { expected_grade_version: input.expectedGradeVersion, decision: input.approve ? 'APPROVE' : 'REJECT', reason: input.reason },
    }, value => {
      const proposal = decodeProposal(value);
      if (proposal.id !== input.proposalId || proposal.status !== (input.approve ? 'APPROVED' : 'REJECTED')) return invalid();
      return proposalModel(proposal);
    }),
    resolveAppeal: input => read('resolveAppeal', {
      pathParams: input, body: { resolution_note: input.resolutionNote, linked_revision_id: input.linkedRevisionId },
    }, value => {
      const appeal = decodeAppeal(value);
      if (appeal.id !== input.appealId || appeal.classId !== input.classId || appeal.status !== 'RESOLVED') return invalid();
      return appealModel(appeal);
    }),
  };
}
