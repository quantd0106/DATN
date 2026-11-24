const classes = '/classes/:classId';
const practices = classes + '/practices';
const practice = practices + '/:practiceId';
const practiceAttempt = practice + '/attempts/:attemptId';
const assessments = classes + '/assessments';
const assessment = assessments + '/:assessmentId';
const assessmentAttempt = assessment + '/attempts/:attemptId';
const assignments = classes + '/assignments';
const assignment = assignments + '/:assignmentId';
const submission = '/submissions/:submissionId';

type LearningOperation = {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE';
  template: string;
  statuses: readonly number[];
  query: readonly string[];
  mode: 'json' | 'binary' | 'empty';
};

function operation(method: LearningOperation['method'], template: string, statuses = [200],
  query: string[] = [], mode: LearningOperation['mode'] = 'json'): LearningOperation {
  return { method, template, statuses, query, mode };
}

export const learningOperations = {
  activities: operation('GET', classes + '/activities', [200], ['kind']),
  deleteActivity: operation('DELETE', classes + '/activities/:activityId', [204], [], 'empty'),
  questionBank: operation('GET', classes + '/question-bank', [200], ['origin', 'type', 'topic']),
  createPractice: operation('POST', practices, [201]),
  practiceEditor: operation('GET', practice + '/editor'),
  savePracticeEditor: operation('PUT', practice + '/draft'),
  attachPracticeItems: operation('POST', practice + '/items/attach'),
  reviewPracticeQuestion: operation('POST', practice + '/questions/:questionId/review'),
  publishPractice: operation('POST', practice + '/publish'),
  newPracticeVersion: operation('POST', practice + '/versions', [201]),
  practices: operation('GET', practices),
  startPractice: operation('POST', practice + '/attempts', [200, 201]),
  practiceAttempt: operation('GET', practiceAttempt),
  savePracticeAttempt: operation('PUT', practiceAttempt + '/draft'),
  checkPracticeAnswer: operation('POST', practiceAttempt + '/check'),
  explainPracticeMistake: operation('POST', practiceAttempt + '/explain-mistake'),
  finishPractice: operation('POST', practiceAttempt + '/finish'),
  practiceResult: operation('GET', practiceAttempt + '/result'),

  createAssessment: operation('POST', assessments, [201]),
  assessmentDraft: operation('GET', assessment + '/draft'),
  saveAssessmentDraft: operation('PUT', assessment + '/draft'),
  reviewAssessmentQuestion: operation('POST', assessment + '/questions/:questionId/review'),
  proposeAssessment: operation('POST', assessment + '/propose'),
  decideAssessment: operation('POST', assessment + '/decision'),
  newAssessmentVersion: operation('POST', assessment + '/versions', [201]),
  assessments: operation('GET', assessments),
  assessment: operation('GET', assessment),
  startAssessment: operation('POST', assessment + '/attempts', [201]),
  assessmentAttempt: operation('GET', assessmentAttempt),
  saveAssessmentAnswers: operation('PUT', assessmentAttempt + '/answers'),
  submitAssessment: operation('POST', assessmentAttempt + '/submit'),
  reconcileAssessment: operation('GET', assessmentAttempt + '/reconcile', [200], ['operation_id']),
  assessmentResult: operation('GET', assessmentAttempt + '/result'),
  assessmentGrading: operation('GET', assessmentAttempt + '/grading'),

  createAssignment: operation('POST', assignments, [201]),
  saveAssignmentDraft: operation('PUT', assignment + '/draft'),
  assignmentRecipients: operation('PUT', assignment + '/recipients', [204], [], 'empty'),
  readAssignmentRecipients: operation('GET', assignment + '/recipients'),
  assignmentGrading: operation('GET', assignment + submission + '/grading'),
  assignmentDueOverride: operation('POST', assignment + '/due-overrides', [201]),
  assignments: operation('GET', assignments),
  assignment: operation('GET', assignment),
  uploadAssignment: operation('POST', assignment + '/uploads', [201]),
  submitAssignment: operation('POST', assignment + '/submissions', [201]),
  reconcileAssignment: operation('GET', assignment + '/submissions/reconcile', [200], ['operation_id']),
  submissionFile: operation('GET', submission + '/files/:fileId', [200], [], 'binary'),
  gradeAssignment: operation('POST', submission + '/grade'),
  assignmentGradeHint: operation('POST', submission + '/grade-hint'),

  gradingQueue: operation('GET', classes + '/grading'),
  gradeAssessment: operation('POST', assessmentAttempt + '/grade'),
  releaseAssessment: operation('POST', assessment + '/release-results'),
  proposeGradeRevision: operation('POST', '/grades/:gradeId/revisions', [201]),
  decideGradeRevision: operation('POST', '/grade-revisions/:revisionId/decision'),
  proposeScoringAdjustment: operation('POST', classes + '/activity-versions/:versionId/scoring-adjustments', [201]),
  decideScoringAdjustment: operation('POST', '/scoring-adjustments/:adjustmentId/decision'),
  createAppeal: operation('POST', classes + '/appeals', [201]),
  resolveAppeal: operation('POST', classes + '/appeals/:appealId/resolve'),
  historyResult: operation('GET', '/history/results/:resultId'),
} as const;

export type LearningOperationId = keyof typeof learningOperations;
export const learningUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Question IDs are opaque strings in Learning. Reject path separators, control
// characters and dot segments, and encode the segment before creating a URL.
function validParameter(name: string, value: string) {
  return name === 'questionId'
    ? [...value].length >= 1 && [...value].length <= 100 && value !== '.' && value !== '..' &&
      !/[\\/\u0000-\u001f\u007f]/u.test(value)
    : learningUuid.test(value);
}

export function learningOperationPath(id: LearningOperationId, params: Record<string, unknown> = {}) {
  return learningOperations[id].template.replace(/:([A-Za-z]+)\b/g, (_, name: string) => {
    const value = params[name];
    if (typeof value !== 'string' || !validParameter(name, value)) throw new Error(`Invalid Learning path parameter ${name}`);
    return encodeURIComponent(value);
  });
}

export function findLearningOperation(method: string, parts: string[]) {
  return Object.values(learningOperations).find(operation => {
    if (operation.method !== method) return false;
    const segments = operation.template.slice(1).split('/');
    return segments.length === parts.length && segments.every((segment, index) =>
      segment.startsWith(':') ? validParameter(segment.slice(1), parts[index]) : segment === parts[index],
    );
  });
}
