import type { AssessmentGrading, AssessmentResult } from './assessment';
import type { CalendarEvent, ClassSummary, RouteParams, TeacherActivityItem } from './domain';

// Frontend read models for assignments.
export type AssignmentSource = {
  id: string;
  title: string;
  version: number;
  accessible?: boolean;
  documentVersionId?: string;
  sourceId?: string;
  documentId?: string;
};
export type AssignmentPolicy = { maxVersions: number; allowLate: boolean; lateUntil: string | null; resubmitUntil: string | null; gradedVersion: 'LATEST' | 'FIRST'; maxBytes: number | null; acceptedTypes: string[]; newEnrollment: 'REVIEW' | 'AUTO'; maxScore?: number; appealWindowDays?: number };
export type AssignmentDefinition = { activityType?: 'ASSIGNMENT' | 'ASSESSMENT'; publishedRevision?: number; id: string; classId: string; title: string; topic: string; instructions: string; dueAt: string; status: 'DRAFT' | 'PUBLISHED'; revision: number; policy: AssignmentPolicy; source?: AssignmentSource; sourceHref?: string; appealWindowDays?: number };
export type SubmissionFile = { id: string; name: string; size: number; type: string };
export type AssignmentGrade = { version: number; score: number; maxScore?: number; feedback: string; teacherId: string | null; teacherName: string; confirmedAt: string; reason: string };
export type AssignmentSubmission = { id: string; assignmentId: string; assignmentRevision: number; classId: string; studentId: string; studentName: string; number: number; file: SubmissionFile; note: string; submittedAt: string; late: boolean; grade: AssignmentGrade | null; gradeHistory: AssignmentGrade[]; isGradedVersion: boolean; resultId: string };
export type AssignmentUpload = { id: string; assignmentId: string; file: SubmissionFile; acknowledgedAt: string; previewOnly: true };
export type GradeAppeal = { officialTarget?: { classId: string; assessmentId: string; attemptId: string }; id: string; classId: string; submissionId: string; studentId: string; reason: string; createdAt: string; status: 'OPEN' | 'PROPOSED' | 'RESOLVED'; resolution?: string };
export type GradeProposal = { officialTarget?: { classId: string; assessmentId: string; attemptId: string }; id: string; appealId: string | null; classId: string; submissionId: string; score: number; maxScore?: number; reason: string; expectedGradeVersion: number; proposerId: string; proposerName: string; proposedAt: string; status: 'PENDING' | 'APPROVED' | 'REJECTED'; decisionReason?: string; decidedAt?: string; decidedBy?: string };
export type GradingQueueItem = { id: string; title: string; studentName: string; number: number; submittedAt: string; late: boolean; graded: boolean; membershipEnded: boolean; href: string; kind: 'ASSIGNMENT' | 'ASSESSMENT'; assessmentId?: string };
export type AssignmentPage =
 | { kind: 'official-history'; classSummary?: ClassSummary; result: AssessmentResult; limitedHistory: boolean }
 | { kind: 'list'; classSummary: ClassSummary; assignments: { assignment: AssignmentDefinition; submissions: AssignmentSubmission[] }[] }
 | { kind: 'detail'; classSummary: ClassSummary; assignment: AssignmentDefinition; submissions: AssignmentSubmission[]; canSubmit: boolean; submitBlockedReason?: string; canAppeal: boolean; appeals: GradeAppeal[]; proposals: GradeProposal[] }
 | { kind: 'history'; classSummary?: ClassSummary; assignment?: AssignmentDefinition; submission: AssignmentSubmission; canAppeal: boolean; appeals: GradeAppeal[]; proposals: GradeProposal[]; limitedHistory: boolean }
 | { kind: 'editor'; classSummary: ClassSummary; assignment: AssignmentDefinition; sources: AssignmentSource[]; audience: {userId: string; assigned: boolean; displayName?: string}[]; audienceRosterAvailable?: boolean }
 | { kind: 'queue'; classSummary: ClassSummary; items: GradingQueueItem[]; appeals: GradeAppeal[]; proposals: GradeProposal[] }
 | { kind: 'grading'; classSummary: ClassSummary; assignment: AssignmentDefinition; submission: AssignmentSubmission; submissions: AssignmentSubmission[]; teachingRole: 'PRIMARY' | 'ASSISTING'; appeals: GradeAppeal[] }
 | { kind: 'correction'; officialEvidence?: AssessmentGrading; classSummary: ClassSummary; assignment: AssignmentDefinition; submission: AssignmentSubmission; appeal: GradeAppeal; proposals: GradeProposal[]; teachingRole: 'PRIMARY' | 'ASSISTING' }
 | { kind: 'appeal-review'; classSummary: ClassSummary; item: GradingQueueItem; appeal?: GradeAppeal; gradeVersion: number; proposal?: GradeProposal; canDecide: boolean; canPropose: boolean; canResolve: boolean; evidence: {kind: 'assessment'; grading: AssessmentGrading} | {kind: 'assignment'; assignment: AssignmentDefinition; submission: AssignmentSubmission; submissions: AssignmentSubmission[]} }
 | { kind: 'approval'; officialEvidence?: AssessmentGrading; classSummary: ClassSummary; assignment: AssignmentDefinition; submission: AssignmentSubmission; appeal: GradeAppeal; proposal: GradeProposal; canDecide: boolean };
export type AssignmentDraftInput = { classId: string; assignmentId: string; title: string; topic: string; instructions: string; dueAt: string; policy: AssignmentPolicy; source?: AssignmentSource; appealWindowDays?: number; expectedRevision: number; publish: boolean };
export interface AssignmentPort {
 listCalendarEvents(classId:string):Promise<CalendarEvent[]>;
 listTeacherActivities(classId: string): Promise<TeacherActivityItem[]>;
 setRecipients(input: {classId: string; assignmentId: string; studentIds: string[]; expectedRevision: number}): Promise<void>;
 createDraft(classId: string): Promise<{assignmentId: string; href: string}>;
 load(routeId: string, params: RouteParams, signal?: AbortSignal): Promise<AssignmentPage>;
 upload(input: { classId: string; assignmentId: string; file: File; expectedRevision: number }): Promise<AssignmentUpload>;
 submit(input: { classId: string; assignmentId: string; uploadId: string; expectedRevision: number; operationId: string; note: string }): Promise<AssignmentSubmission>;
 reconcile(input: { classId: string; assignmentId: string; operationId: string }): Promise<AssignmentSubmission | null>;
 readFile(input: { submissionId: string; fileId: string }): Promise<{ name: string; type: string; bytes: Uint8Array }>;
 saveDraft(input: AssignmentDraftInput): Promise<AssignmentDefinition>;
 grade(input: { submissionId: string; score: number; feedback: string; expectedGradeVersion: number }): Promise<AssignmentGrade>;
 gradeHint(input: { submissionId: string }): Promise<{ feedback: string; limitations: string }>;
 appeal(input: { submissionId: string; classId?: string; reason: string }): Promise<GradeAppeal>;
 propose(input: { classId: string; appealId: string; score: number; reason: string; expectedGradeVersion: number }): Promise<GradeProposal>;
 decide(input: { proposalId: string; approve: boolean; reason: string; expectedGradeVersion: number }): Promise<GradeProposal>;
 resolveAppeal?(input: { classId: string; appealId: string; resolutionNote: string; linkedRevisionId?: string }): Promise<GradeAppeal>;
}

// The page audience is explicit: a staff review must not enter a learner-only route.
export function submissionResultHref(
  submission: Pick<AssignmentSubmission, 'id' | 'classId' | 'assignmentId' | 'resultId'>,
  audience: 'student' | 'teacher',
): string {
  if (audience === 'teacher') return `/teacher/classes/${encodeURIComponent(submission.classId)}/assignments/${encodeURIComponent(submission.assignmentId)}/submissions/${encodeURIComponent(submission.id)}/grade`;
  return `/student/history/results/${encodeURIComponent(submission.resultId)}`;
}

const PREVIEW_MIME_BY_EXTENSION: Record<string, string> = {
  pdf: 'application/pdf',
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  webp: 'image/webp',
  gif: 'image/gif',
  txt: 'text/plain',
  md: 'text/markdown',
  markdown: 'text/markdown',
};

function detectMimeFromBytes(bytes?: Uint8Array): string | null {
  if (!bytes || bytes.length < 4) return null;
  if (bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46) return 'application/pdf';
  if (bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) return 'image/png';
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return 'image/jpeg';
  if (bytes[0] === 0x47 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x38) return 'image/gif';
  if (
    bytes.length >= 12 &&
    bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46 &&
    bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50
  ) {
    return 'image/webp';
  }
  return null;
}

export function inferPreviewMimeType(fileName: string, declaredType: string, bytes?: Uint8Array): string {
  const baseDeclared = (declaredType || '').split(';')[0]?.trim().toLowerCase() ?? '';
  if (baseDeclared && baseDeclared !== 'application/octet-stream') {
    return baseDeclared;
  }
  const byBytes = detectMimeFromBytes(bytes);
  if (byBytes) return byBytes;
  const dot = fileName.lastIndexOf('.');
  const ext = dot >= 0 ? fileName.slice(dot + 1).toLowerCase() : '';
  return PREVIEW_MIME_BY_EXTENSION[ext] ?? 'application/octet-stream';
}

export type SubmissionPreviewKind = 'pdf' | 'image' | 'text' | 'download';

export function detectSubmissionPreviewKind(
  fileName: string,
  declaredType: string,
  bytes?: Uint8Array,
): SubmissionPreviewKind {
  const mime = inferPreviewMimeType(fileName, declaredType, bytes);
  if (mime === 'application/pdf') return 'pdf';
  if (/^image\/(jpeg|png|webp|gif)$/.test(mime)) return 'image';
  if (mime === 'text/plain' || mime === 'text/markdown') return 'text';
  return 'download';
}
