import { routeHref } from '@/config/routes';
import { aiOperations } from '../api/ai-operations';
import { learningUuid } from '../api/learning-operations';
import { decodeMutation } from '../api/decoders';
import { ApiError } from '../api/errors';
import type { AiHttpRequest } from '../api/http';
import { assessmentAttemptHref, type AssessmentQuestion, type AssessmentSource } from '../assessment';
import type { CitationItem, HistoryClassData, PracticeQuestion, QuestionType, TeacherTutorInstructionConfig, TutorConversation, TutorMessage, WorkspaceData } from '../domain';
import { boolean, enumeration, integer, learningReader, list, nullable, number, optional, optionalServiceRead, readHistoryClass, record, text, timestamp, uuid } from './learning-wire';
import type { LearningCore } from './learning-wire';
import type { LiveDocumentContext } from './live-document';
import type { LiveIdentitySession } from './live-identity';
import type { DataAdapter } from './port';

const tutorUnavailable = 'Trợ lý AI Lumi hiện chưa sẵn sàng trên máy chủ.';
export const quizSourceUnavailable = 'Nguồn tài liệu lớp chưa sẵn sàng để sinh câu hỏi AI.';

function invalid(): never {
  throw new ApiError('CONTRACT_MISMATCH', 'Dữ liệu dịch vụ AI không hợp lệ.', 502);
}

function decodeAi<T>(decode: (value: unknown) => T, value: unknown): T {
  try { return decode(value); }
  catch (error) { if (error instanceof ApiError && error.code === 'CONTRACT_MISMATCH') return invalid(); throw error; }
}

function sourceLocator(location: Record<string, unknown> | undefined) {
  if (location?.page != null) return `Trang ${integer(location.page)}`;
  if (location?.slide != null) return `Slide ${integer(location.slide)}`;
  return optional(location?.section, text) ?? '';
}

function decodeMessage(value: unknown, classId: string, conversationId: string): TutorMessage {
  const row = record(value);
  const base = { id: uuid(row.id), conversationId, timestamp: timestamp(row.created_at) };
  if (enumeration(row.role, 'USER', 'ASSISTANT') === 'USER') {
    return { ...base, role: 'user', content: optional(row.content, text) ?? '' };
  }
  const status = enumeration(row.status, 'COMPLETED', 'STREAMING', 'STOPPED', 'FAILED');
  const rendered = optional(row.rendered, record);
  const claims = list(rendered?.claims ?? [], record);
  const step = optional(rendered?.step, record);
  const stepState = optional(step?.state, value =>
    enumeration(value, 'PLANNED', 'HINT', 'IN_PROGRESS', 'SOLVED', 'REVEALED'));
  const seen = new Set<string>();
  const citations: CitationItem[] = [];
  for (const claim of claims) {
    for (const cite of list(claim.citations ?? [], record)) {
      const sourceId = optional(cite.source_id, text) ?? '';
      const location = optional(cite.location, record);
      const isAccessible = cite.masked !== true && location !== undefined;
      const locator = sourceLocator(location);
      const quote = optional(cite.quote, text) ?? '';
      const excerpt = isAccessible ? quote : '';
      const rawDocumentId = optional(cite.document_id, text);
      const validDocumentId = rawDocumentId && learningUuid.test(rawDocumentId) ? rawDocumentId.toLowerCase() : undefined;
      const pageNumber = isAccessible ? optional(location?.page, integer) : undefined;
      const baseReaderHref = isAccessible && validDocumentId
        ? routeHref('s.reader', { classId, documentId: validDocumentId }) : null;
      const readerHref = !isAccessible ? '#'
        : baseReaderHref ? (pageNumber !== undefined ? `${baseReaderHref}?page=${pageNumber}` : baseReaderHref)
        : (routeHref('s.documents', { classId }) ?? '#');
      const documentTitle = optional(location?.document_name, text) ?? 'Tài liệu lớp học';
      const key = JSON.stringify([sourceId, documentTitle, location?.page, location?.slide, location?.section, quote, isAccessible]);
      if (seen.has(key)) continue;
      seen.add(key);
      citations.push({
        id: `${base.id}:${citations.length}`,
        documentId: rawDocumentId ?? sourceId,
        documentTitle, locator, excerpt, isAccessible,
        pageNumber,
        readerHref,
      });
    }
  }
  const claimText = claims.map(claim => optional(claim.text, text)?.trim()).filter(Boolean).join('\n\n');
  const notice = optional(rendered?.notice, text);
  const fallback = status === 'STOPPED' ? 'Câu trả lời đã dừng trước khi hoàn tất.'
    : status === 'FAILED' ? 'Lumi chưa thể trả lời. Bạn có thể thử lại.' : '';
  return {
    ...base, role: 'assistant', content: claimText || notice || fallback, citations,
    stepNumber: optional(step?.step, integer), totalSteps: optional(step?.total_steps, integer),
    ...(stepState ? { stepState } : {}),
    isIncomplete: status === 'STOPPED', isFailed: status === 'FAILED', isStreaming: status === 'STREAMING',
    knowledgeType: claims.some(claim => claim.basis === 'source') ? 'CLASS_SOURCE' : 'GENERAL_KNOWLEDGE',
  };
}

function decodeConversation(value: unknown, classId: string): TutorConversation {
  const row = record(value);
  if (uuid(row.class_id) !== classId) return invalid();
  return { id: uuid(row.id), classId, title: text(row.title), updatedAt: timestamp(row.updated_at), preview: '', messages: [] };
}

function decodeConfig(value: unknown, classId: string): TeacherTutorInstructionConfig {
  const row = record(value);
  const version = nullable(row.version, value => integer(value, 1));
  const hintRevealAfter = integer(row.hint_reveal_after, 1);
  const maxAnswerWords = integer(row.max_answer_words, 40);
  if (hintRevealAfter > 10 || maxAnswerWords > 300) return invalid();
  return {
    classId, instructions: '', updatedAt: version === null ? 'Mặc định hệ thống' : `Phiên bản ${version}`,
    maxChars: 2000,
    disclaimer: 'Giáo viên chỉ cấu hình chính sách sư phạm chung của lớp. Lịch sử hỏi đáp cá nhân của học sinh được giữ riêng tư.',
    policy: {
      allowGeneralKnowledge: boolean(row.allow_general_knowledge),
      hintRevealAfter,
      allowSolutionReveal: boolean(row.allow_solution_reveal),
      guidanceLevel: enumeration(row.guidance_level, 'light', 'balanced', 'strong'),
      maxAnswerWords, version,
    },
  };
}

type QuizInput = {
  classId: string;
  documentVersionId: string;
  purpose: 'PRACTICE' | 'ASSESSMENT_DRAFT';
  type: QuestionType;
  count: number;
  signal?: AbortSignal;
};

function proposalMatrix(value: unknown, type: string, total: number) {
  const matrix = list(value, entry => {
    const row = record(entry);
    return {
      section: Array.isArray(row.section) ? list(row.section, nonEmpty) : nonEmpty(row.section),
      level: enumeration(row.level, 'biet', 'hieu', 'van_dung'),
      count: integer(row.count, 1), type,
    };
  });
  if (!matrix.length) throw new ApiError('UNAVAILABLE', 'AI chưa tìm được nội dung để sinh câu hỏi từ nguồn này.', 503);
  const proposed = matrix.reduce((sum, row) => sum + row.count, 0);
  if (proposed > total) return invalid();
  // AI can propose fewer slots when the source has few facts. The job may return fewer usable drafts.
  matrix[0].count += total - proposed;
  // Overriding the item type can collapse cells that originally differed only by type.
  const cells = new Map<string, (typeof matrix)[number]>();
  for (const row of matrix) {
    const key = JSON.stringify([row.section, row.level]);
    const existing = cells.get(key);
    if (existing) existing.count += row.count;
    else cells.set(key, row);
  }
  return [...cells.values()];
}

function nonEmpty(value: unknown) {
  const result = text(value).trim();
  return result || invalid();
}

function decodeQuizItem(id: string, item: Record<string, unknown>, type: QuestionType, points: number) {
  if (item.type !== (type === 'true_false' ? 'true_false_4' : type)) return invalid();
  const base = { id, prompt: nonEmpty(item.stem ?? item.context ?? item.prompt), points, explanation: optional(item.explanation, text) ?? '' };
  const practice = { ...base, isAiProposed: true, isReviewed: false };
  const assessment = { ...base, rubric: optional(item.rubric, text) ?? '', aiProposed: true, reviewed: false };
  if (type === 'single_choice' || type === 'multiple_choice') {
    const correct = type === 'single_choice' ? [nonEmpty(item.correct_answer)] : list(item.correct_answers, nonEmpty);
    const distractors = list(item.distractors, value => nonEmpty(typeof value === 'string' ? value : record(value).text));
    if (correct.length < (type === 'single_choice' ? 1 : 2) || !distractors.length) return invalid();
    const options = [...correct, ...distractors].map((text, index) => ({ id: `opt-${index + 1}`, label: String.fromCharCode(65 + index), text }));
    const correctIds = options.slice(0, correct.length).map(option => option.id);
    return {
      practice: type === 'single_choice'
        ? { ...practice, type, options, correctOptionId: correctIds[0] }
        : { ...practice, type, options, correctOptionIds: correctIds },
      assessment: { ...assessment, type, options, key: type === 'single_choice' ? correctIds[0] : correctIds },
    };
  }
  if (type === 'true_false') {
    const statements = list(item.statements, value => {
      const row = record(value);
      return { text: nonEmpty(row.text), isCorrect: boolean(row.truth) };
    }).map((row, index) => ({ ...row, id: `stmt-${index + 1}`, label: String.fromCharCode(97 + index) }));
    if (statements.length !== 4) return invalid();
    return {
      practice: { ...practice, type, statements },
      assessment: { ...assessment, type, statements: statements.map(({ id, label, text }) => ({ id, label, text })),
        key: Object.fromEntries(statements.map(statement => [statement.id, statement.isCorrect])) },
    };
  }
  const answer = optional(item.answer, record);
  const expectedAnswer = nonEmpty(answer?.value ?? answer?.canonical ?? item.expected_answer);
  const acceptedAnswers = list(answer?.accepted ?? [], nonEmpty).slice(0, 5);
  const tolerance = optional(answer?.tolerance, number) ?? 1e-6;
  if (tolerance < 0) return invalid();
  const manualGradingRequired = !answer && item.manual_grading_required === true;
  return {
    practice: { ...practice, type, expectedAnswer, acceptedAnswers, tolerance, manualGradingRequired },
    assessment: { ...assessment, type, key: expectedAnswer, acceptedAnswers, tolerance, manualGradingRequired },
  };
}

function quizDraft(id: string, item: Record<string, unknown>, type: QuestionType) {
  const decoded = decodeQuizItem(id, item, type, 1);
  const evidence = [
    ...list(item.evidence ?? [], record),
    ...list(item.statements ?? [], value => list(record(value).evidence ?? [], record)).flat(),
  ];
  const sourceFields = (source: AssessmentSource) => ({
    document_id: source.documentId,
    document_version_id: source.documentVersionId,
    source_id: source.sourceId,
    source_spans: evidence.filter(citation => citation.source_id === source.sourceId)
      .flatMap(citation => list(citation.source_spans ?? [], record)),
  });
  return {
    id,
    practiceQuestion(source: AssessmentSource, points: number): PracticeQuestion {
      const sourceRef = {
        ...sourceFields(source), documentId: source.documentId, documentTitle: source.title,
        versionNumber: source.version, locator: source.locator, displayText: source.title, isAccessible: true,
      };
      return { ...decoded.practice, points, sourceRef };
    },
    assessmentQuestion(source: AssessmentSource): AssessmentQuestion {
      return {
        ...decoded.assessment,
        source: { ...source, wire: { ...source.wire, ...sourceFields(source), title: source.title,
          version: source.version, locator: source.locator, accessible: source.accessible } },
      };
    },
  };
}

function pollDelay(ms: number, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort);
      resolve();
    }, ms);
    signal.addEventListener('abort', abort, { once: true });
  });
}

function aiReader(session: LiveIdentitySession) {
  return async function read<T>(id: Parameters<AiHttpRequest>[0], options: Parameters<AiHttpRequest>[1], decode: (value: unknown) => T) {
    const mutation = aiOperations[id].method !== 'GET';
    const decoder = (value: unknown) => decodeAi(decode, value);
    return session.authenticatedAi(async request => {
      const value = await request(id, options);
      return mutation ? decodeMutation(decoder, value) : decoder(value);
    }, mutation);
  };
}

function checkSession(session: LiveIdentitySession, generation: number, mutation = false) {
  if (session.sessionGeneration() !== generation) {
    throw new ApiError('SESSION_CHANGED', 'Phiên đăng nhập đã thay đổi. Hãy tải lại nội dung.', 401, mutation ? 'unknown' : 'rejected');
  }
}

type QuizOptions = { pollDelayMs?: number };
type LiveAiOptions = QuizOptions & { documentContext?: LiveDocumentContext };

export async function generateQuizDrafts(
  { classId, documentVersionId, purpose, type, count, signal }: QuizInput,
  session: LiveIdentitySession,
  { pollDelayMs = 250 }: QuizOptions = {},
) {
  if (!Number.isInteger(count) || count < 1 || count > 100) {
    throw new ApiError('BAD_REQUEST', 'Số câu hỏi AI phải từ 1 đến 100.', 400);
  }
  signal?.throwIfAborted();
  const read = aiReader(session);
  const generation = session.sessionGeneration();
  const pathParams = { classId };
  const scope = [{ document_version_id: documentVersionId }];
  const aiType = type === 'true_false' ? 'true_false_4' : type;
  const matrix = await read('proposeQuiz', { pathParams, body: { purpose, scope, total: count }, signal },
    value => proposalMatrix(value, aiType, count));
  checkSession(session, generation);
  const jobId = await read('createQuizJob', { pathParams, body: { purpose, scope, matrix }, signal }, value => {
    const row = record(value);
    enumeration(row.status, 'QUEUED');
    return uuid(row.id);
  });

  const deadline = AbortSignal.timeout(60_000);
  const pollSignal = signal ? AbortSignal.any([signal, deadline]) : deadline;
  const jobParams = { classId, jobId };
  try {
    while (true) {
      checkSession(session, generation);
      const status = await read('getQuizJob', { pathParams: jobParams, signal: pollSignal }, value => {
        const row = record(value);
        if (uuid(row.id) !== jobId || uuid(row.class_id) !== classId) return invalid();
        return enumeration(row.status, 'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED');
      });
      if (status === 'COMPLETED') break;
      if (status === 'FAILED') throw new ApiError('QUIZ_JOB_FAILED', 'AI không thể sinh câu hỏi từ nguồn đã chọn.', 502);
      await pollDelay(pollDelayMs, pollSignal);
    }
  } catch (error) {
    if (deadline.aborted && !signal?.aborted) throw new ApiError('QUIZ_JOB_TIMEOUT', 'AI chưa sinh xong câu hỏi trong thời gian chờ.', 504);
    throw error;
  }
  checkSession(session, generation);
  return read('listQuizJobItems', { pathParams: jobParams, signal }, value => {
    const drafts = list(value, entry => {
      const row = record(entry);
      return row.item === null ? null : quizDraft(uuid(row.id), record(row.item), type);
    }).filter(draft => draft !== null);
    if (!drafts.length) throw new ApiError('UNAVAILABLE', 'AI không trả về câu hỏi có thể dùng từ nguồn hiện tại.', 503);
    return drafts;
  });
}

export function createLiveAi(session: LiveIdentitySession, core: Pick<LearningCore, 'readClass'>, options: LiveAiOptions = {}) {
  const read = aiReader(session);
  const learning = learningReader(session);

  async function loadAssessmentLock(classId: string, signal?: AbortSignal) {
    try {
      return await learning('assessments', { pathParams: { classId }, signal }, value => {
        const active = list(record(value).items, record).find(row => row.active_attempt_id != null);
        return active ? {
          isOfficialAssessmentActive: true, assessmentTitle: text(active.title),
          assessmentHref: assessmentAttemptHref({ classId, assessmentId: uuid(active.id), attemptId: uuid(active.active_attempt_id) }),
        } : { isOfficialAssessmentActive: false };
      });
    } catch (error) {
      if (error instanceof ApiError && error.status !== 401) return { isOfficialAssessmentActive: false };
      throw error;
    }
  }

  type Stream = { generation: number; started: Promise<string | null>; stop?: Promise<void> };
  const activeStreams = new Map<string, Stream>();
  const tutor: DataAdapter['tutor'] = {
    async updateConfig() {
      throw new ApiError('UNAVAILABLE', 'Cấu hình Lumi trên máy chủ sử dụng các trường chính sách sư phạm.', 503);
    },
    updatePolicyConfig: (classId, policy) => read('updateTutorConfig', {
      pathParams: { classId },
      body: {
        allow_general_knowledge: policy.allowGeneralKnowledge, hint_reveal_after: policy.hintRevealAfter,
        allow_solution_reveal: policy.allowSolutionReveal, guidance_level: policy.guidanceLevel,
        max_answer_words: policy.maxAnswerWords, expected_version: policy.expectedVersion,
      },
    }, value => decodeConfig(value, classId)),
    createConversation(classId, title) {
      const name = [...(title?.trim() || 'Cuộc trò chuyện mới')].slice(0, 200).join('');
      return read('createTutorConversation', {
        pathParams: { classId },
        body: { title: name, default_mode: 'STEP_BY_STEP' },
      }, value => ({ id: uuid(record(value).id), classId, title: name, updatedAt: new Date().toISOString(), preview: '', messages: [] }));
    },
    async sendMessage(input) {
      const { classId, conversationId, mode, signal } = input;
      signal?.throwIfAborted();
      const generation = session.sessionGeneration();
      if (activeStreams.get(conversationId)?.generation === generation) {
        throw new ApiError('CONFLICT', 'Cuộc trò chuyện đang có câu trả lời chưa hoàn tất.', 409);
      }
      let markStarted!: (id: string | null) => void;
      const started = new Promise<string | null>(resolve => { markStarted = resolve; });
      const stream: Stream = { generation, started };
      activeStreams.set(conversationId, stream);
      let messageId: string | undefined;
      let finalMessage: TutorMessage | undefined;
      const operation = input.operation ?? 'ASK';
      const retryOfMessageId =
        input.isRetry && input.retryUserMessageId && learningUuid.test(input.retryUserMessageId)
          ? input.retryUserMessageId
          : undefined;
      try {
        return await session.authenticatedAi(async request => {
          await request('sendTutorMessage', {
            pathParams: { conversationId }, signal,
            body: {
              operation,
              mode,
              ...(operation === 'ASK' || operation === 'CHECK' ? { text: input.content } : {}),
              ...(retryOfMessageId ? { retry_of_message_id: retryOfMessageId } : {}),
            },
            onEvent({ event, data }) {
              checkSession(session, generation, true);
              if (event === 'start') {
                if (messageId) invalid();
                messageId = decodeAi(value => uuid(record(value).message_id), data);
                markStarted(messageId);
              }
              if (event === 'message') {
                const message = decodeAi(value => decodeMessage(value, classId, conversationId), data);
                if (message.id !== messageId || message.role !== 'assistant' || message.isStreaming || finalMessage) invalid();
                finalMessage = message;
                input.onChunk?.(message.content);
              }
            },
          });
          if (!finalMessage) throw new ApiError('INVALID_RESPONSE', 'Luồng AI kết thúc khi chưa có câu trả lời xác nhận.', 502, 'unknown');
          return finalMessage;
        }, true);
      } finally {
        markStarted(null);
        if (activeStreams.get(conversationId) === stream) activeStreams.delete(conversationId);
      }
    },
    async stopStreaming(conversationId) {
      const stream = activeStreams.get(conversationId);
      if (!stream || stream.generation !== session.sessionGeneration()) return;
      stream.stop ??= stream.started.then(async messageId => {
        if (!messageId) return;
        checkSession(session, stream.generation, true);
        await read('stopTutorMessage', { pathParams: { conversationId, messageId } }, value => boolean(record(value).stopped));
      }).catch(error => {
        stream.stop = undefined;
        throw error;
      });
      await stream.stop;
    },
  };

  const workspace: DataAdapter['workspace'] & { handles(routeId: string): boolean } = {
    handles: routeId => routeId === 's.tutor' || routeId === 't.tutor' || routeId === 's.history',
    async load(routeId, params, signal): Promise<WorkspaceData> {
      const { classId } = params;
      const generation = session.sessionGeneration();
      try {
        if (routeId === 't.tutor') {
          const [classSummary, teacherTutorConfig] = await Promise.all([
            core.readClass(classId, signal),
            read('getTutorConfig', { pathParams: { classId }, signal }, value => decodeConfig(value, classId)),
          ]);
          checkSession(session, generation);
          return { kind: 'ready', classSummary, teacherTutorConfig };
        }
        if (routeId === 's.history') {
          if (!classId) throw new ApiError('NOT_FOUND', 'Không tìm thấy lớp học.', 404);
          const docContext = options.documentContext;
          const [classSummary, conversations, classNotes] = await Promise.all([
            readHistoryClass(core, classId, signal),
            read('listTutorConversations', { pathParams: { classId }, signal }, value => list(value, item => decodeConversation(item, classId))),
            docContext
              ? optionalServiceRead(() => docContext.listClassNotes(classId, signal), [], signal)
              : Promise.resolve([]),
          ]);
          checkSession(session, generation);
          const historyConversations = await Promise.all(
            conversations.map(async conv => {
              const messages = await read(
                'listTutorMessages',
                { pathParams: { conversationId: conv.id }, signal },
                value => list(value, item => decodeMessage(item, classId, conv.id)),
              );
              return {
                id: conv.id,
                title: conv.title,
                date: conv.updatedAt,
                preview: messages.at(-1)?.content ?? '',
                messages: messages.map(msg => ({
                  role: msg.role,
                  content: msg.content,
                  timestamp: msg.timestamp,
                })),
              };
            }),
          );
          checkSession(session, generation);
          const summary = classSummary ?? {
            id: classId,
            code: 'LỚP HỌC',
            name: 'Lịch sử lớp học',
            status: 'ARCHIVED' as const,
            metadata: 'Lịch sử ghi danh cá nhân',
            membershipStatus: 'ENDED' as const,
          };
          const historyData: HistoryClassData = {
            summary,
            historyNotice: summary.membershipStatus === 'ENDED'
              ? 'Ghi danh đã kết thúc. Dữ liệu hội thoại AI Tutor trước đây được giữ ở chế độ CHỈ ĐỌC theo quy định bảo lưu.'
              : 'Lớp học đang ở trạng thái lưu trữ. Các cuộc hội thoại với Lumi được giữ ở chế độ CHỈ ĐỌC.',
            conversations: historyConversations,
            ...(classNotes.length ? {
              notes: classNotes.map(note => ({
                id: note.id,
                title: `${note.documentTitle}${note.locator ? ` · ${note.locator}` : ''}`,
                date: note.updatedAt,
                content: note.noteText,
              })),
            } : {}),
          };
          return { kind: 'ready', ...(classSummary ? { classSummary } : {}), historyData };
        }
        if (routeId !== 's.tutor') throw new ApiError('NOT_FOUND', 'Không tìm thấy trang trợ lý AI.', 404);
        const docContext = options.documentContext;
        const [classSummary, conversations, lock, classHasSources, quickNotes] = await Promise.all([
          core.readClass(classId, signal),
          read('listTutorConversations', { pathParams: { classId }, signal }, value => list(value, item => decodeConversation(item, classId))),
          loadAssessmentLock(classId, signal),
          docContext
            ? optionalServiceRead(() => docContext.listClassDocuments(classId, signal).then(docs => docs.length > 0), true, signal)
            : Promise.resolve(true),
          docContext
            ? optionalServiceRead(() => docContext.listClassNotes(classId, signal), [], signal)
            : Promise.resolve([]),
        ]);
        checkSession(session, generation);
        const active = params.compose === 'new' ? undefined
          : conversations.find(conversation => conversation.id === params.conversationId) ?? conversations[0];
        if (active) {
          active.messages = await read('listTutorMessages', { pathParams: { conversationId: active.id }, signal },
            value => list(value, item => decodeMessage(item, classId, active.id)));
          active.preview = active.messages.at(-1)?.content ?? '';
        }
        checkSession(session, generation);
        return { kind: 'ready', classSummary, tutorWorkspace: {
          classSummary, conversations, activeConversationId: active?.id ?? null, ...lock, classHasSources,
          citations: active?.messages.filter(message => message.role === 'assistant').at(-1)?.citations ?? [], quickNotes,
        } };
      } catch (error) {
        if (error instanceof ApiError && error.status === 503) return { kind: 'unavailable', message: tutorUnavailable };
        throw error;
      }
    },
  };

  return { tutor, workspace, generateQuizDrafts: (input: QuizInput) => generateQuizDrafts(input, session, options) };
}

export type LiveQuizGenerator = Pick<ReturnType<typeof createLiveAi>, 'generateQuizDrafts'>;
