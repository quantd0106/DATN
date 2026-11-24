import { ApiError } from '../api/errors';
import type { LearningSourceReference, PracticeQuestion } from '../domain';
import type { PracticeAuthoringPort, PracticeAuthoringScope, PracticeEditorSnapshot } from '../practice-authoring';
import { quizSourceUnavailable, type LiveQuizGenerator } from './live-ai';
import type { LiveIdentitySession } from './live-identity';
import { boolean, integer, learningReader, list, number, optional, questionType, record, requireLearningSession, text, timestamp, uuid, type LearningCore } from './learning-wire';

export function practiceSource(value: unknown): LearningSourceReference {
  const row = record(value);
  return {
    documentId: text(row.documentId ?? row.document_id ?? row.id ?? ''),
    documentTitle: text(row.documentTitle ?? row.title ?? ''),
    versionNumber: integer(row.versionNumber ?? row.version_number ?? row.version ?? 1, 1),
    locator: text(row.locator ?? ''),
    displayText: text(row.displayText ?? row.documentTitle ?? row.title ?? ''),
    isAccessible: row.isAccessible === true || row.accessible === true,
    pageNumber: optional(row.pageNumber, value => integer(value, 1)),
  };
}

export function practiceQuestion(value: unknown, editor: boolean): PracticeQuestion {
  const row = record(value);
  const base = {
    // Keep authoring extensions across a save, including bank provenance and grading metadata.
    ...(editor ? row : {}),
    id: text(row.id), prompt: text(row.prompt),
    points: number(row.points), topic: optional(row.topic, text),
    sourceReference: optional(row.sourceReference, text),
    sourceRef: editor && row.sourceRef != null ? { ...record(row.sourceRef), ...practiceSource(row.sourceRef) } : optional(row.sourceRef, practiceSource),
    ...(editor ? { explanation: optional(row.explanation, text), isReviewed: optional(row.isReviewed, boolean) } : {}),
  };
  const type = questionType(row.type);
  const choices = (value: unknown, statements = false) => list(value, entry => {
    const choice = record(entry);
    return {
      ...(editor ? choice : {}),
      id: text(choice.id), text: text(choice.text), label: optional(choice.label, text) ?? '',
      ...(editor && statements ? { isCorrect: optional(choice.isCorrect, boolean) } : {}),
    };
  });
  if (type === 'single_choice') return { ...base, type, options: choices(row.options), correctOptionId: editor ? optional(row.correctOptionId, text) : undefined };
  if (type === 'multiple_choice') return { ...base, type, options: choices(row.options), correctOptionIds: editor ? optional(row.correctOptionIds, v => list(v, text)) : undefined };
  if (type === 'true_false') return { ...base, type, statements: choices(row.statements, true) };
  return { ...base, type, expectedAnswer: editor ? optional(row.expectedAnswer, text) : undefined,
    manualGradingRequired: editor ? optional(row.manualGradingRequired, boolean) : undefined };
}

function decodeSnapshot(value: unknown): PracticeEditorSnapshot {
  const row = record(value);
  const editor = record(row.editor);
  const revision = text(row.revision);
  if (!/^[1-9]\d*$/.test(revision) || !Number.isSafeInteger(Number(revision))) {
    throw new ApiError('CONTRACT_MISMATCH', 'Phiên bản bản nháp không hợp lệ.', 502);
  }
  return {
    revision, defaultPoints: number(row.defaultPoints), questionTypes: list(row.questionTypes, questionType),
    editor: {
      practiceId: uuid(editor.practiceId), classId: uuid(editor.classId), className: text(editor.className),
      title: text(editor.title), topic: text(editor.topic), versionLabel: text(editor.versionLabel),
      currentVersionNumber: integer(editor.currentVersionNumber, 1), isDraft: boolean(editor.isDraft),
      questions: list(editor.questions, value => practiceQuestion(value, true)),
      reviewedCount: integer(editor.reviewedCount), totalQuestions: integer(editor.totalQuestions),
      canPublish: boolean(editor.canPublish),
      availableClassSources: list(editor.availableClassSources, value => {
        const source = record(value);
        return {
          id: uuid(source.id), title: text(source.title), version: integer(source.version, 1),
          isReady: boolean(source.isReady), documentVersionId: optional(source.documentVersionId, uuid),
          sourceId: optional(source.sourceId, text),
        };
      }),
    },
  };
}

function revision(value: string) {
  const parsed = Number(value);
  if (!/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(parsed)) {
    throw new ApiError('BAD_REQUEST', 'Phiên bản bản nháp không hợp lệ.', 400);
  }
  return { expected_revision: parsed };
}

export function createLivePracticeAuthoring(session: LiveIdentitySession, core: LearningCore, ai: LiveQuizGenerator) {
  const read = learningReader(session);
  let cached: { generation: number; snapshot: PracticeEditorSnapshot } | undefined;
  const decodeScopedSnapshot = (value: unknown, scope: PracticeAuthoringScope) => {
    const snapshot = decodeSnapshot(value);
    if (snapshot.editor.classId !== scope.classId || snapshot.editor.practiceId !== scope.practiceId) {
      throw new ApiError('CONTRACT_MISMATCH', 'Bản nháp không thuộc bài luyện tập đã yêu cầu.', 502);
    }
    return snapshot;
  };
  async function remember<T extends { snapshot: PracticeEditorSnapshot }>(load: () => Promise<T>) {
    const generation = session.sessionGeneration();
    const result = await load();
    requireLearningSession(session, generation);
    cached = { generation, snapshot: result.snapshot };
    return result;
  }
  const readSnapshot = async (scope: PracticeAuthoringScope, signal?: AbortSignal) =>
    (await remember(() => read('practiceEditor', { pathParams: scope, signal }, value => ({ snapshot: decodeScopedSnapshot(value, scope) })))).snapshot;
  const save = (input: PracticeAuthoringScope & {
    expectedRevision: string; title?: string; topic?: string; questions: PracticeQuestion[];
  }) => remember(() => read('savePracticeEditor', {
    pathParams: input,
    body: { ...revision(input.expectedRevision), title: input.title, topic: input.topic, questions: input.questions },
  }, value => ({ snapshot: decodeScopedSnapshot(value, input), savedAt: timestamp(record(value).saved_at) })));

  const port: PracticeAuthoringPort = {
    async load(scope, signal) {
      const [snapshot, classSummary] = await Promise.all([readSnapshot(scope, signal), core.readClass(scope.classId, signal)]);
      return { ...snapshot, classSummary, editor: { ...snapshot.editor, className: classSummary.name } };
    },
    async save({ content, ...input }) { return (await save({ ...input, ...content })).snapshot; },
    review: async input => (await remember(() => read('reviewPracticeQuestion', {
      pathParams: input, body: { ...revision(input.expectedRevision), approved: input.approved },
    }, value => ({ snapshot: decodeScopedSnapshot(value, input) })))).snapshot,
    publish: input => read('publishPractice', {
      pathParams: input, body: revision(input.expectedRevision),
    }, value => ({ publishedVersionId: uuid(record(value).published_version_id) })),
    async propose(input) {
      const generation = session.sessionGeneration();
      const current = cached?.generation === generation ? cached.snapshot : undefined;
      const snapshot = current?.editor.classId === input.classId && current.editor.practiceId === input.practiceId
        ? current : await readSnapshot(input);
      const source = snapshot.editor.availableClassSources.find(item => item.isReady &&
        ((item.id === input.sourceId && item.version === input.sourceVersion) || item.documentVersionId === input.sourceId));
      if (!source?.documentVersionId) throw new ApiError('UNAVAILABLE', quizSourceUnavailable, 503);
      requireLearningSession(session, generation);
      const [draft] = await ai.generateQuizDrafts({
        classId: input.classId, documentVersionId: source.documentVersionId, purpose: 'PRACTICE', type: input.type, count: 1,
      });
      return {
        id: draft.id, sourceId: input.sourceId, sourceVersion: input.sourceVersion,
        question: draft.practiceQuestion({
          documentId: source.id, documentVersionId: source.documentVersionId, sourceId: source.sourceId,
          title: source.title, version: source.version, accessible: true, locator: '',
        }, snapshot.defaultPoints),
      };
    },
    async createNewVersion(scope) {
      const generation = session.sessionGeneration();
      await read('newPracticeVersion', { pathParams: scope }, () => undefined);
      requireLearningSession(session, generation);
      return port.load(scope);
    },
  };
  return { ...port, readSnapshot, saveEditor: save };
}
