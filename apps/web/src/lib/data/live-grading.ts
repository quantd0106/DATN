import { routeHref } from '@/config/routes';
import { ApiError } from '../api/errors';
import type { AssessmentGradeHistory } from '../assessment';
import type { GradeAppeal, GradeProposal, GradingQueueItem } from '../assignment';
import type { LiveIdentitySession } from './live-identity';
import { boolean, dictionary, enumeration, integer, invalid, learningReader, list, nullable, number, optional, record, text, timestamp, uuid } from './learning-wire';

const targetType = (value: unknown) => enumeration(value, 'ASSESSMENT_ATTEMPT', 'ASSIGNMENT_SUBMISSION');

export function decodeGrade(value: unknown) {
  const row = record(value);
  return {
    gradeId: uuid(row.grade_id), gradeVersion: integer(row.grade_version), version: integer(row.version, 1),
    score: number(row.total_score), maxScore: number(row.max_score),
    scores: nullable(row.scores, value => dictionary(value, entry => nullable(entry, number))),
    feedback: text(row.feedback), reason: text(row.reason),
    teacherId: nullable(row.teacher_id, uuid), teacherName: optional(row.teacher_name, text) ?? '',
    confirmedAt: nullable(row.confirmed_at, timestamp),
  };
}

export function gradeHistory(value: unknown): AssessmentGradeHistory {
  const grade = decodeGrade(value);
  if (grade.confirmedAt === null) return invalid();
  return {
    version: grade.version, score: grade.score, maxScore: grade.maxScore, feedback: grade.feedback,
    reason: grade.reason, at: grade.confirmedAt, approvedBy: grade.teacherName,
  };
}

export function decodeAppeal(value: unknown) {
  const row = record(value);
  return {
    id: uuid(row.id), classId: uuid(row.class_id), studentId: uuid(row.student_id),
    gradeId: uuid(row.grade_id), targetType: targetType(row.target_type), submissionId: uuid(row.target_id),
    questionId: nullable(row.question_id, text), reason: text(row.reason),
    status: enumeration(row.status, 'OPEN', 'PROPOSED', 'RESOLVED'), createdAt: timestamp(row.created_at),
    resolution: optional(row.resolution_note, text),
  };
}

export function decodeProposal(value: unknown) {
  const row = record(value);
  const grade = decodeGrade(row);
  const status = enumeration(row.status, 'PROPOSED', 'APPROVED', 'REJECTED');
  return {
    ...grade, id: uuid(row.id), appealId: nullable(row.appeal_id, uuid), classId: uuid(row.class_id),
    submissionId: uuid(row.target_id), targetType: targetType(row.target_type),
    expectedGradeVersion: integer(row.base_version), proposerId: uuid(row.proposed_by),
    proposedAt: timestamp(row.proposed_at), status: status === 'PROPOSED' ? 'PENDING' as const : status,
    decisionReason: optional(row.decision_reason, text), decidedAt: grade.confirmedAt ?? undefined,
    decidedBy: grade.teacherId ?? undefined,
  };
}

function decodeQueueItem(value: unknown, classId: string) {
  const row = record(value);
  if (row.class_id !== classId) return invalid();
  const kind = enumeration(row.kind, 'ASSIGNMENT', 'ASSESSMENT');
  const id = uuid(row.id), activityId = uuid(row.activity_id);
  const href = kind === 'ASSESSMENT'
    ? routeHref('t.examGrading', { classId, assessmentId: activityId, attemptId: id })
    : routeHref('t.assignmentGrading', { classId, assignmentId: activityId, submissionId: id });
  return {
    id, kind, classId, activityId, studentId: uuid(row.student_id), studentName: text(row.student_name),
    title: text(row.title), submittedAt: timestamp(row.submitted_at), number: integer(row.number, 1),
    late: boolean(row.late), graded: boolean(row.graded), membershipEnded: boolean(row.membership_ended),
    assessmentId: kind === 'ASSESSMENT' ? activityId : undefined,
    gradeId: nullable(row.grade_id, uuid), gradeVersion: integer(row.grade_version), href: href ?? invalid(),
    released: boolean(row.released), effectiveScore: nullable(row.effective_score, number), feedback: text(row.feedback),
    gradeHistory: list(row.grade_history, gradeHistory),
    appealId: nullable(row.appeal_id, uuid), appealReason: nullable(row.appeal_reason, text),
  } satisfies GradingQueueItem & Record<string, unknown>;
}

export type QueueRow = ReturnType<typeof decodeQueueItem>;
export type WireProposal = ReturnType<typeof decodeProposal>;
export type WireAppeal = ReturnType<typeof decodeAppeal>;

function officialTarget(item: Pick<QueueRow, 'classId' | 'activityId' | 'id' | 'kind'> | undefined) {
  return item?.kind === 'ASSESSMENT'
    ? { classId: item.classId, assessmentId: item.activityId, attemptId: item.id } : undefined;
}
export function appealModel(appeal: WireAppeal, item?: QueueRow): GradeAppeal {
  return { ...appeal, officialTarget: officialTarget(item) };
}
export function proposalModel(proposal: WireProposal, item?: QueueRow): GradeProposal {
  return { ...proposal, officialTarget: officialTarget(item), proposerName: '' };
}
export function findQueueRow(items: QueueRow[], kind: QueueRow['kind'], activityId: string, id: string) {
  const item = items.find(row => row.kind === kind && row.id === id && row.activityId === activityId);
  if (!item) throw new ApiError('NOT_FOUND', 'Không tìm thấy bài đã nộp.', 404);
  return item;
}
export function createGradingReader(session: LiveIdentitySession) {
  const read = learningReader(session);
  return (classId: string, signal?: AbortSignal) => read('gradingQueue', { pathParams: { classId }, signal }, value => {
    const row = record(value);
    const items = list(row.items, value => decodeQueueItem(value, classId));
    const appeals = list(row.appeals, decodeAppeal);
    const proposals = list(row.proposals, decodeProposal);
    if (appeals.some(appeal => appeal.classId !== classId) || proposals.some(proposal => proposal.classId !== classId)) return invalid();
    return { items, appeals, proposals };
  });
}
