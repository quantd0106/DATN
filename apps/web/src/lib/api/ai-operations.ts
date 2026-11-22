import { learningUuid } from './learning-operations';

type AiOperation = {
  method: 'GET' | 'POST' | 'PUT';
  template: string;
  status: number;
  query: readonly string[];
  mode: 'json' | 'sse';
};

function operation(method: AiOperation['method'], template: string, status = 200,
  query: string[] = [], mode: AiOperation['mode'] = 'json'): AiOperation {
  return { method, template, status, query, mode };
}

const tutor = '/classes/:classId/tutor';
const messages = '/tutor/conversations/:conversationId/messages';
const quiz = '/classes/:classId/quiz';

export const aiOperations = {
  listTutorConversations: operation('GET', tutor + '/conversations', 200, ['limit']),
  createTutorConversation: operation('POST', tutor + '/conversations', 201),
  listTutorMessages: operation('GET', messages, 200, ['limit']),
  sendTutorMessage: operation('POST', messages, 200, [], 'sse'),
  stopTutorMessage: operation('POST', messages + '/:messageId/stop'),
  getTutorConfig: operation('GET', tutor + '/config'),
  updateTutorConfig: operation('PUT', tutor + '/config'),
  proposeQuiz: operation('POST', quiz + '/proposals'),
  listQuizJobs: operation('GET', quiz + '/jobs', 200, ['limit']),
  createQuizJob: operation('POST', quiz + '/jobs', 202),
  getQuizJob: operation('GET', quiz + '/jobs/:jobId'),
  listQuizJobItems: operation('GET', quiz + '/jobs/:jobId/items'),
  reviewQuizItem: operation('POST', quiz + '/items/:draftId/review'),
  regenerateQuizItem: operation('POST', quiz + '/items/:draftId/regenerate'),
  exportQuizItem: operation('POST', quiz + '/items/:draftId/export'),
} as const;

export type AiOperationId = keyof typeof aiOperations;
export const aiStreamTimeoutMs = 190_000;

export function aiOperationPath(id: AiOperationId, params: Record<string, unknown> = {}) {
  return aiOperations[id].template.replace(/:([A-Za-z]+)\b/g, (_, name: string) => {
    const value = params[name];
    if (typeof value !== 'string' || !learningUuid.test(value)) throw new Error(`Invalid AI path parameter ${name}`);
    return encodeURIComponent(value);
  });
}

export function findAiOperation(method: string, parts: string[]) {
  return Object.entries(aiOperations).find(([, operation]) => {
    if (operation.method !== method) return false;
    const segments = operation.template.slice(1).split('/');
    return segments.length === parts.length && segments.every((segment, index) =>
      segment.startsWith(':') ? learningUuid.test(parts[index]) : segment === parts[index]);
  });
}
