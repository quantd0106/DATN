import { createLiveIntelligencePort } from './live-intelligence';
import { createLiveIdentitySession } from './live-identity';
import { createLiveCore } from './live-core';
import { createLiveAccounts } from './live-accounts';
import { createLiveDocument } from './live-document';
import { createLiveClassroomAdminPort } from './live-classroom-admin';
import { createLivePracticeAuthoring } from './live-practice-authoring';
import { createLiveAssignment } from './live-assignment';
import { createLiveAssessment } from './live-assessment';
import { createLiveLearning } from './live-learning';
import { createLiveAi } from './live-ai';
import { createLiveDashboardEnricher } from './live-dashboard';
import type { DataAdapter } from './port';
import { ApiError } from '../api/errors';

const session = createLiveIdentitySession();
const core = createLiveCore(session);
const accounts = createLiveAccounts(session);
const document = createLiveDocument(session, core.classroomReads);
const liveAi = createLiveAi(session, core, {
  documentContext: {
    listClassDocuments: document.listClassDocuments,
    listClassNotes: document.listClassNotes,
  },
});
const practiceAuthoring = createLivePracticeAuthoring(session, core, liveAi);
const learning = createLiveLearning(session, core, practiceAuthoring);
const dashboard = createLiveDashboardEnricher(session, {
  listClassDocuments: document.listClassDocuments,
});

export const adapter: DataAdapter = {
  source: 'live',
  personalData: { async download() { throw new ApiError('UNAVAILABLE', 'Vui lòng xem hướng dẫn bàn giao trong ghi chú xử lý hoặc liên hệ nhà trường để nhận bản sao.', 503); } },
  intelligence: createLiveIntelligencePort(session),
  classroom: createLiveClassroomAdminPort(session),
  practiceAuthoring,
  accounts,
  assignment: createLiveAssignment(session, core),
  assessment: createLiveAssessment(session, core, liveAi),
  identity: core.identity,
  social: core.social,
  calendar: core.calendar,
  notifications: core.notifications,
  document: document.document,
  tutor: liveAi.tutor,
  learning: learning.learning,
  workspace: {
    async load(routeId, params, signal) {
      if (liveAi.workspace.handles(routeId)) return liveAi.workspace.load(routeId, params, signal);
      if (learning.workspace.handles(routeId)) return learning.workspace.load(routeId, params, signal);
      const docData = document.loadWorkspace(routeId, params, signal);
      if (docData) return docData;
      return dashboard.enrich(routeId, params, await core.workspace.load(routeId, params, signal), signal);
    },
  },
};
