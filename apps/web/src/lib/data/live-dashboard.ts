import { routeHref } from '@/config/routes';
import { inferCalendarEventCategory, parseLearningActivityRef } from '@/lib/calendar';
import { isRouteAvailable } from '@/lib/route-availability';
import type {
  CalendarEvent,
  ClassOverviewData,
  PracticeActivity,
  StudentHomeOverview,
  WorkspaceData,
} from '../domain';
import { decodeSummary } from './assessment-wire';
import {
  learningReader,
  list,
  optionalServiceRead,
  record,
  requireLearningSession,
} from './learning-wire';
import { decodeAssignmentSummary } from './live-assignment';
import type { LiveDocumentContext } from './live-document';
import { createGradingReader } from './live-grading';
import type { LiveIdentitySession } from './live-identity';
import { createTeacherActivityReader, decodePracticeList } from './live-learning';

function countPendingGrading(queue: Awaited<ReturnType<ReturnType<typeof createGradingReader>>>) {
  return (
    queue.items.filter(item => !item.graded).length +
    queue.proposals.filter(proposal => proposal.status === 'PENDING').length +
    queue.appeals.filter(appeal => appeal.status === 'OPEN').length
  );
}

function formatDue(iso: string) {
  const parsed = Date.parse(iso);
  if (!Number.isFinite(parsed)) return iso;
  return new Date(parsed).toLocaleString('vi-VN', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function daysLeftBadge(iso: string, nowMs: number) {
  const parsed = Date.parse(iso);
  if (!Number.isFinite(parsed)) return 'Đang mở';
  const diffMs = parsed - nowMs;
  if (diffMs < 0) return 'Đã qua hạn';
  if (diffMs < 3_600_000) return 'Sắp hết hạn';
  const days = Math.ceil(diffMs / 86_400_000);
  return days <= 1 ? 'Còn hôm nay' : `Còn ${days} ngày`;
}

function practiceProgress(activity: PracticeActivity) {
  if (activity.status === 'IN_PROGRESS') {
    const answered = Math.min(activity.questionCount, (activity.currentQuestionIndex ?? 0) + 1);
    const percent =
      activity.questionCount > 0
        ? Math.min(95, Math.max(10, Math.round((answered / activity.questionCount) * 100)))
        : 10;
    return {
      percent,
      text: `Câu ${answered}/${activity.questionCount}`,
      actionLabel: 'Tiếp tục luyện tập',
    };
  }
  if (activity.status === 'COMPLETED') {
    const percent = activity.lastScore ? Math.round(activity.lastScore.percentage) : 100;
    return {
      percent,
      text: activity.lastScore
        ? `Đạt ${activity.lastScore.correct}/${activity.lastScore.total} (${percent}%)`
        : 'Đã hoàn thành',
      actionLabel: 'Xem kết quả',
    };
  }
  return {
    percent: 0,
    text: `Chưa bắt đầu (${activity.questionCount} câu)`,
    actionLabel: 'Bắt đầu',
  };
}

export function createLiveDashboardEnricher(
  session: LiveIdentitySession,
  documentContext: Pick<LiveDocumentContext, 'listClassDocuments'>,
) {
  const read = learningReader(session);
  const teacherActivities = createTeacherActivityReader(session);
  const gradingQueue = createGradingReader(session);

  function readAssessments(classId: string, signal?: AbortSignal) {
    return optionalServiceRead(
      () =>
        read('assessments', { pathParams: { classId }, signal }, raw =>
          list(record(raw).items, decodeSummary),
        ),
      [],
      signal,
    );
  }

  function readAssignments(classId: string, signal?: AbortSignal) {
    return optionalServiceRead(
      () =>
        read('assignments', { pathParams: { classId }, signal }, raw =>
          list(record(raw).items, decodeAssignmentSummary),
        ),
      [],
      signal,
    );
  }

  function readPractices(classId: string, signal?: AbortSignal) {
    return optionalServiceRead(
      () => read('practices', { pathParams: { classId }, signal }, decodePracticeList),
      null,
      signal,
    );
  }

  async function enrichStudentHome(
    data: Extract<WorkspaceData, { kind: 'ready' }>,
    signal?: AbortSignal,
  ): Promise<WorkspaceData> {
    const home = data.studentHome;
    if (!home) return data;
    const activeClasses = home.activeClasses;
    const nowMs = Date.now();
    const classPayloads = await Promise.all(
      activeClasses.map(async cls => {
        const [assessments, assignments, practices] = await Promise.all([
          readAssessments(cls.id, signal),
          readAssignments(cls.id, signal),
          readPractices(cls.id, signal),
        ]);
        return { cls, assessments, assignments, practices };
      }),
    );

    const taskCandidates: {
      sortMs: number;
      task: StudentHomeOverview['upcomingTasks'][number];
    }[] = [];
    const studyCandidates: {
      rank: number;
      completedAt: string;
      study: StudentHomeOverview['recentStudies'][number];
    }[] = [];

    for (const { cls, assessments, assignments, practices } of classPayloads) {
      for (const item of assessments) {
        if (item.status === 'CLOSED' && !item.activeAttemptId) continue;
        if (!item.activeAttemptId && !item.canStart && item.status !== 'UPCOMING') continue;
        const targetIso = item.status === 'UPCOMING' ? item.opensAt : item.closesAt;
        const href =
          (item.activeAttemptId
            ? routeHref('s.examAttempt', {
                classId: cls.id,
                assessmentId: item.id,
                attemptId: item.activeAttemptId,
              })
            : routeHref('s.assessment', { classId: cls.id, assessmentId: item.id })) ??
          `/student/classes/${cls.id}/assessments/${item.id}`;
        taskCandidates.push({
          sortMs: Number.isFinite(Date.parse(targetIso))
            ? Date.parse(targetIso)
            : Number.MAX_SAFE_INTEGER,
          task: {
            id: `${cls.id}:exam:${item.id}`,
            type: 'EXAM',
            title: item.title,
            className: cls.name,
            dueDetail:
              item.status === 'UPCOMING'
                ? `Mở ${formatDue(item.opensAt)}`
                : `Hạn ${formatDue(item.closesAt)}`,
            daysLeftLabel:
              item.status === 'UPCOMING' ? 'Sắp mở' : daysLeftBadge(item.closesAt, nowMs),
            href,
            actionLabel: item.activeAttemptId
              ? 'Tiếp tục làm'
              : item.status === 'OPEN'
                ? 'Làm bài'
                : 'Xem chi tiết',
          },
        });
      }

      for (const item of assignments) {
        if (!item.dueAt) continue;
        const href =
          routeHref('s.assignment', { classId: cls.id, assignmentId: item.id }) ??
          `/student/classes/${cls.id}/assignments/${item.id}`;
        taskCandidates.push({
          sortMs: Number.isFinite(Date.parse(item.dueAt))
            ? Date.parse(item.dueAt)
            : Number.MAX_SAFE_INTEGER,
          task: {
            id: `${cls.id}:assignment:${item.id}`,
            type: 'ASSIGNMENT',
            title: item.title,
            className: cls.name,
            dueDetail: `Hạn ${formatDue(item.dueAt)}`,
            daysLeftLabel: daysLeftBadge(item.dueAt, nowMs),
            href,
            actionLabel: 'Xem & Nộp',
          },
        });
      }

      for (const activity of practices?.activities ?? []) {
        const progress = practiceProgress(activity);
        const href =
          (activity.status === 'IN_PROGRESS' && activity.activeAttemptId
            ? routeHref('s.practiceAttempt', {
                classId: cls.id,
                practiceId: activity.id,
                attemptId: activity.activeAttemptId,
              })
            : activity.status === 'COMPLETED' && activity.latestResultAttemptId
              ? routeHref('s.practiceResult', {
                  classId: cls.id,
                  practiceId: activity.id,
                  attemptId: activity.latestResultAttemptId,
                })
              : routeHref('s.practice', { classId: cls.id })) ??
          `/student/classes/${cls.id}/practice`;
        studyCandidates.push({
          rank: activity.status === 'IN_PROGRESS' ? 0 : activity.status === 'COMPLETED' ? 1 : 2,
          completedAt: activity.lastCompletedAt ?? '',
          study: {
            id: `${cls.id}:practice:${activity.id}`,
            type: 'PRACTICE',
            title: activity.title,
            className: cls.name,
            progressText: progress.text,
            progressPercent: progress.percent,
            href,
            actionLabel: progress.actionLabel,
            ...(activity.topic ? { extraNote: `Chủ đề: ${activity.topic}` } : {}),
          },
        });
      }
    }

    taskCandidates.sort((a, b) => a.sortMs - b.sortMs);
    studyCandidates.sort(
      (a, b) => a.rank - b.rank || b.completedAt.localeCompare(a.completedAt),
    );
    const tutorSuggestions = isRouteAvailable('s.tutor', 'live')
      ? activeClasses.slice(0, 3).map(cls => ({
          classId: cls.id,
          prompt: `Hỏi Athena ôn tập kiến thức lớp ${cls.name}`,
        }))
      : [];

    return {
      ...data,
      studentHome: {
        ...home,
        upcomingTasks: taskCandidates.slice(0, 5).map(entry => entry.task),
        recentStudies: studyCandidates.slice(0, 4).map(entry => entry.study),
        tutorSuggestions,
      },
    };
  }

  async function enrichTeacherHome(
    data: Extract<WorkspaceData, { kind: 'ready' }>,
    signal?: AbortSignal,
  ): Promise<WorkspaceData> {
    const home = data.teacherHome;
    if (!home) return data;
    const activeClasses = home.assignedClasses.filter(cls => cls.status === 'ACTIVE');
    if (!activeClasses.length) {
      return {
        ...data,
        teacherHome: {
          ...home,
          stats: {
            totalClasses: home.stats?.totalClasses ?? home.assignedClasses.length,
            totalStudents: home.stats?.totalStudents ?? 0,
            pendingGrading: 0,
          },
        },
      };
    }

    const queues = await Promise.all(
      activeClasses.map(cls =>
        optionalServiceRead(() => gradingQueue(cls.id, signal), null, signal),
      ),
    );
    const todaySchedule = await enrichCalendarEvents(home.todaySchedule, 'LECTURER', signal);
    const succeeded = queues.filter(
      (queue): queue is NonNullable<(typeof queues)[number]> => queue !== null,
    );
    if (!succeeded.length) {
      return {
        ...data,
        teacherHome: {
          ...home,
          todaySchedule,
        },
      };
    }

    const pendingGrading = succeeded.reduce((sum, queue) => sum + countPendingGrading(queue), 0);
    return {
      ...data,
      teacherHome: {
        ...home,
        todaySchedule,
        stats: {
          totalClasses: home.stats?.totalClasses ?? home.assignedClasses.length,
          totalStudents: home.stats?.totalStudents ?? 0,
          pendingGrading,
        },
      },
    };
  }

  async function enrichCalendarEvents(
    events: CalendarEvent[],
    role: 'STUDENT' | 'LECTURER',
    signal?: AbortSignal,
  ): Promise<CalendarEvent[]> {
    const classIds = new Set<string>();
    for (const event of events) {
      if (event.readOnly && event.classId && parseLearningActivityRef(event.externalRef)) {
        classIds.add(event.classId);
      }
    }
    if (!classIds.size) return events;

    type MatchInfo = { kind: 'ASSESSMENT' | 'ASSIGNMENT' | 'PRACTICE'; href?: string };
    const matchesByClass = new Map<string, Map<string, MatchInfo>>();

    await Promise.all(
      [...classIds].map(async classId => {
        const classMatches = new Map<string, MatchInfo>();
        if (role === 'LECTURER') {
          const listItems = await optionalServiceRead(
            () => teacherActivities(classId, undefined, signal),
            [],
            signal,
          );
          for (const item of listItems) {
            classMatches.set(item.resourceId, {
              kind: item.type,
              href: item.submissionsHref ?? item.editHref,
            });
          }
        } else {
          const [assessments, assignments] = await Promise.all([
            readAssessments(classId, signal),
            readAssignments(classId, signal),
          ]);
          for (const item of assessments) {
            const href =
              (item.activeAttemptId
                ? routeHref('s.examAttempt', {
                    classId,
                    assessmentId: item.id,
                    attemptId: item.activeAttemptId,
                  })
                : routeHref('s.assessment', { classId, assessmentId: item.id })) ??
              `/student/classes/${classId}/assessments/${item.id}`;
            classMatches.set(item.id, { kind: 'ASSESSMENT', href });
          }
          for (const item of assignments) {
            const href =
              routeHref('s.assignment', { classId, assignmentId: item.id }) ??
              `/student/classes/${classId}/assignments/${item.id}`;
            classMatches.set(item.id, { kind: 'ASSIGNMENT', href });
          }
        }
        matchesByClass.set(classId, classMatches);
      }),
    );

    return events.map(event => {
      if (!event.readOnly) return event;
      const activityId = parseLearningActivityRef(event.externalRef);
      const matched =
        activityId && event.classId ? matchesByClass.get(event.classId)?.get(activityId) : undefined;
      if (!matched) return event;
      return {
        ...event,
        category: inferCalendarEventCategory({
          origin: 'LEARNING',
          title: event.title,
          learningKind: matched.kind,
        }),
        ...(matched.href ? { href: matched.href } : {}),
      };
    });
  }

  async function enrichStudentOverview(
    data: Extract<WorkspaceData, { kind: 'ready' }>,
    classId: string,
    signal?: AbortSignal,
  ): Promise<WorkspaceData> {
    const overview = data.classOverview;
    if (!overview) return data;
    const [docs, assessments, assignments, practices] = await Promise.all([
      optionalServiceRead(() => documentContext.listClassDocuments(classId, signal), [], signal),
      readAssessments(classId, signal),
      readAssignments(classId, signal),
      readPractices(classId, signal),
    ]);

    const recentMaterials: ClassOverviewData['recentMaterials'] = [...docs]
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      .slice(0, 4)
      .map(doc => ({
        id: doc.id,
        title: doc.title,
        format: doc.format,
        detail: doc.locatorCountLabel ?? `Phiên bản ${doc.currentVersion}`,
        href:
          routeHref('s.reader', { classId, documentId: doc.id }) ??
          `/student/classes/${classId}/documents/${doc.id}`,
        sourceMeta: `v${doc.currentVersion}`,
      }));

    const activities: { rank: number; sortMs: number; item: ClassOverviewData['upcomingActivities'][number] }[] = [];
    for (const item of assessments) {
      const closed = item.status === 'CLOSED' && !item.activeAttemptId;
      activities.push({
        rank: item.activeAttemptId || item.status === 'OPEN' ? 0 : item.status === 'UPCOMING' ? 1 : 3,
        sortMs: Date.parse(item.closesAt) || Number.MAX_SAFE_INTEGER,
        item: {
          id: `exam:${item.id}`,
          type: 'EXAM',
          title: item.title,
          dueDetail: item.status === 'UPCOMING' ? `Mở ${formatDue(item.opensAt)}` : `Hạn ${formatDue(item.closesAt)}`,
          badgeLabel: item.status === 'OPEN' ? 'Đang mở' : item.status === 'UPCOMING' ? 'Sắp mở' : 'Đã đóng',
          badgeStyle: item.status === 'OPEN' ? 'danger' : 'warning',
          href:
            (item.activeAttemptId
              ? routeHref('s.examAttempt', { classId, assessmentId: item.id, attemptId: item.activeAttemptId })
              : routeHref('s.assessment', { classId, assessmentId: item.id })) ??
            `/student/classes/${classId}/assessments/${item.id}`,
          actionLabel: item.activeAttemptId ? 'Tiếp tục làm' : !closed && item.canStart ? 'Làm bài' : 'Xem chi tiết',
          isOfficial: true,
          duration: `${item.durationMinutes} phút`,
        },
      });
    }
    for (const item of assignments) {
      activities.push({
        rank: 1,
        sortMs: Date.parse(item.dueAt) || Number.MAX_SAFE_INTEGER,
        item: {
          id: `assignment:${item.id}`,
          type: 'ASSIGNMENT',
          title: item.title,
          dueDetail: item.dueAt ? `Hạn ${formatDue(item.dueAt)}` : 'Chưa có hạn nộp',
          badgeLabel: 'Bài tập',
          badgeStyle: 'info',
          href:
            routeHref('s.assignment', { classId, assignmentId: item.id }) ??
            `/student/classes/${classId}/assignments/${item.id}`,
          actionLabel: 'Xem & Nộp',
        },
      });
    }
    for (const item of practices?.activities ?? []) {
      activities.push({
        rank: item.status === 'COMPLETED' ? 2 : 1,
        sortMs: Number.MAX_SAFE_INTEGER,
        item: {
          id: `practice:${item.id}`,
          type: 'PRACTICE',
          title: item.title,
          dueDetail: `${item.questionCount} câu hỏi${item.topic ? ` · ${item.topic}` : ''}`,
          badgeLabel: item.status === 'IN_PROGRESS' ? 'Đang làm' : item.status === 'COMPLETED' ? 'Đã hoàn thành' : 'Luyện tập',
          badgeStyle: 'info',
          href:
            (item.activeAttemptId
              ? routeHref('s.practiceAttempt', { classId, practiceId: item.id, attemptId: item.activeAttemptId })
              : routeHref('s.practice', { classId })) ?? `/student/classes/${classId}/practice`,
          actionLabel: item.status === 'IN_PROGRESS' ? 'Tiếp tục' : 'Luyện tập',
        },
      });
    }

    activities.sort((a, b) => a.rank - b.rank || a.sortMs - b.sortMs);
    return {
      ...data,
      classOverview: {
        ...overview,
        recentMaterials,
        upcomingActivities: activities.slice(0, 5).map(entry => entry.item),
      },
    };
  }

  async function enrichTeacherOverview(
    data: Extract<WorkspaceData, { kind: 'ready' }>,
    classId: string,
    signal?: AbortSignal,
  ): Promise<WorkspaceData> {
    const overview = data.classOverview;
    if (!overview) return data;
    const [activityList, queue] = await Promise.all([
      optionalServiceRead(() => teacherActivities(classId, undefined, signal), null, signal),
      optionalServiceRead(() => gradingQueue(classId, signal), null, signal),
    ]);

    const upcomingActivities: ClassOverviewData['upcomingActivities'] = activityList
      ? activityList.slice(0, 5).map(item => ({
          id: item.id,
          type: item.type === 'ASSESSMENT' ? 'EXAM' : item.type,
          title: item.title,
          dueDetail: item.dueOrTimeLabel,
          badgeLabel:
            item.status === 'PUBLISHED'
              ? 'Đang mở'
              : item.status === 'PENDING_APPROVAL'
                ? 'Chờ duyệt'
                : 'Bản nháp',
          badgeStyle: item.status === 'PUBLISHED' ? 'info' : 'warning',
          href:
            item.submissionsHref ??
            item.editHref ??
            routeHref('t.activities', { classId }) ??
            `/teacher/classes/${classId}/activities`,
          actionLabel: item.submissionsHref ? 'Xem bài nộp' : 'Mở hoạt động',
        }))
      : overview.upcomingActivities;

    const teacherMetrics =
      queue !== null && activityList !== null
        ? {
            pendingGrading: countPendingGrading(queue),
            openActivities: activityList.filter(item => item.status === 'PUBLISHED').length,
          }
        : data.teacherMetrics;

    return {
      ...data,
      ...(teacherMetrics ? { teacherMetrics } : {}),
      classOverview: {
        ...overview,
        upcomingActivities,
      },
    };
  }

  async function enrich(
    routeId: string,
    params: Record<string, string>,
    baseData: WorkspaceData,
    signal?: AbortSignal,
  ): Promise<WorkspaceData> {
    if (baseData.kind !== 'ready') return baseData;
    const generation = session.sessionGeneration();
    let result: WorkspaceData = baseData;
    if (routeId === 's.home') result = await enrichStudentHome(baseData, signal);
    else if (routeId === 't.home') result = await enrichTeacherHome(baseData, signal);
    else if (routeId === 's.overview' && params.classId) {
      result = await enrichStudentOverview(baseData, params.classId, signal);
    } else if (routeId === 't.overview' && params.classId) {
      result = await enrichTeacherOverview(baseData, params.classId, signal);
    } else if (routeId === 's.calendar' && baseData.calendarEvents) {
      result = {
        ...baseData,
        calendarEvents: await enrichCalendarEvents(baseData.calendarEvents, 'STUDENT', signal),
      };
    } else if (routeId === 't.calendar' && baseData.calendarEvents) {
      result = {
        ...baseData,
        calendarEvents: await enrichCalendarEvents(baseData.calendarEvents, 'LECTURER', signal),
      };
    } else if (routeId === 'account.calendar' && baseData.calendarEvents) {
      const current = session.currentUser();
      const calRole = current?.roles.includes('LECTURER') ? 'LECTURER' : 'STUDENT';
      result = {
        ...baseData,
        calendarEvents: await enrichCalendarEvents(baseData.calendarEvents, calRole, signal),
      };
    }
    requireLearningSession(session, generation);
    return result;
  }

  return { enrich };
}
