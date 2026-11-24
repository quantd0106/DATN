import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  countNotificationsByCategory,
  filterNotifications,
  formatNotificationEventWindow,
  formatUnreadBadgeCount,
  getNotificationCategoryLabel,
  getNotificationCategoryShortLabel,
} from '../src/lib/notifications';
import { emitNotificationStateChanged, onNotificationStateChanged } from '../src/lib/notification-events';
import { decodeCoreNotification } from '../src/lib/data/live-core';
import type { NotificationItem, User } from '../src/lib/domain';

const CLASS_ID = '00000000-0000-4000-8000-000000000101';
const EVENT_ID = '00000000-0000-4000-8000-000000000201';
const PRACTICE_ID = '00000000-0000-4000-8000-000000000301';

const studentUser: User = {
  id: '00000000-0000-4000-8000-000000000011',
  email: null,
  username: 'student.demo',
  school_id: '00000000-0000-4000-8000-000000000001',
  display_name: 'Học sinh Demo',
  roles: ['STUDENT'],
  status: 'ACTIVE',
};

const teacherUser: User = {
  ...studentUser,
  id: '00000000-0000-4000-8000-000000000012',
  roles: ['LECTURER'],
  username: null,
  email: 'teacher@example.com',
  display_name: 'Giáo viên Demo',
};

const classMap = new Map([[CLASS_ID, 'Toán 9A']]);

test('formatUnreadBadgeCount formats 0, 1..99, and 99+ accurately', () => {
  assert.equal(formatUnreadBadgeCount(0), '');
  assert.equal(formatUnreadBadgeCount(-2), '');
  assert.equal(formatUnreadBadgeCount(5), '5');
  assert.equal(formatUnreadBadgeCount(99), '99');
  assert.equal(formatUnreadBadgeCount(120), '99+');
});

test('category labels and filters classify calendar, practice, assignment, exam, and discussion items', () => {
  assert.equal(getNotificationCategoryLabel('CALENDAR'), 'Lịch học');
  assert.equal(getNotificationCategoryLabel('PRACTICE'), 'Luyện tập');
  assert.equal(getNotificationCategoryLabel('ASSIGNMENT'), 'Bài tập');
  assert.equal(getNotificationCategoryLabel('EXAM'), 'Kiểm tra');
  assert.equal(getNotificationCategoryShortLabel('DISCUSSION'), 'Thảo luận');

  const items: NotificationItem[] = [
    { id: '1', userId: 'u1', title: 'Lịch mới', content: '...', category: 'CALENDAR', createdAt: '2026-10-16T08:00:00Z', isRead: false },
    { id: '2', userId: 'u1', title: 'Luyện tập', content: '...', category: 'PRACTICE', createdAt: '2026-10-16T08:00:00Z', isRead: false },
    { id: '3', userId: 'u1', title: 'Bài tập', content: '...', category: 'ASSIGNMENT', createdAt: '2026-10-16T08:00:00Z', isRead: true },
    { id: '4', userId: 'u1', title: 'Kiểm tra', content: '...', category: 'EXAM', createdAt: '2026-10-16T08:00:00Z', isRead: false },
  ];

  const counts = countNotificationsByCategory(items);
  assert.equal(counts.CALENDAR, 1);
  assert.equal(counts.PRACTICE, 1);
  assert.equal(counts.ASSIGNMENT, 1);
  assert.equal(counts.EXAM, 1);

  assert.deepEqual(
    filterNotifications(items, { readFilter: 'unread', category: 'PRACTICE' }).map(i => i.id),
    ['2'],
  );
  assert.deepEqual(
    filterNotifications(items, { readFilter: 'unread', category: 'ASSIGNMENT' }).map(i => i.id),
    [],
  );
  assert.deepEqual(
    filterNotifications(items, { readFilter: 'all', category: 'ALL' }).map(i => i.id),
    ['1', '2', '3', '4'],
  );
});

test('decodeCoreNotification maps calendar lifecycle, learning deadlines by activity_kind, practice, and feed events', () => {
  const created = decodeCoreNotification(
    {
      id: 'notif-cal-created',
      type: 'CALENDAR_EVENT_CREATED',
      read: false,
      created_at: '2026-11-24T01:00:00Z',
      payload_json: {
        class_id: CLASS_ID,
        calendar_event_id: EVENT_ID,
        version: 1,
        starts_at: '2026-11-25T00:00:00Z',
        ends_at: '2026-11-25T02:15:00Z',
      },
    },
    studentUser,
    classMap,
  );
  assert.equal(created.category, 'CALENDAR');
  assert.equal(created.title, 'Lịch học / sự kiện mới');
  assert.equal(created.targetHref, `/student/classes/${CLASS_ID}/calendar`);
  assert.match(created.content, /07:00 – 09:15/);
  assert.match(created.content, /Toán 9A/);

  const updated = decodeCoreNotification(
    {
      id: 'notif-cal-updated',
      type: 'CALENDAR_EVENT_UPDATED',
      read: false,
      created_at: '2026-11-24T02:00:00Z',
      payload_json: { class_id: CLASS_ID, calendar_event_id: EVENT_ID, version: 2 },
    },
    studentUser,
    classMap,
  );
  assert.equal(updated.category, 'CALENDAR');
  assert.equal(updated.title, 'Lịch học / sự kiện đã cập nhật');

  const practice = decodeCoreNotification(
    {
      id: 'notif-practice',
      type: 'PRACTICE_PUBLISHED',
      read: false,
      created_at: '2026-11-24T02:30:00Z',
      payload_json: { class_id: CLASS_ID, resource_type: 'practice', resource_id: PRACTICE_ID },
    },
    studentUser,
    classMap,
  );
  assert.equal(practice.category, 'PRACTICE');
  assert.equal(practice.targetHref, `/student/classes/${CLASS_ID}/practice`);

  const assignmentDeadline = decodeCoreNotification(
    {
      id: 'notif-assign-dl',
      type: 'CALENDAR_DEADLINE_CHANGED',
      read: false,
      created_at: '2026-11-24T03:00:00Z',
      payload_json: { class_id: CLASS_ID, event_id: EVENT_ID, version: 1, activity_kind: 'ASSIGNMENT' },
    },
    studentUser,
    classMap,
  );
  assert.equal(assignmentDeadline.category, 'ASSIGNMENT');
  assert.equal(assignmentDeadline.targetHref, `/student/classes/${CLASS_ID}/assignments`);

  const examDeadline = decodeCoreNotification(
    {
      id: 'notif-exam-dl',
      type: 'CALENDAR_DEADLINE_CHANGED',
      read: false,
      created_at: '2026-11-24T03:05:00Z',
      payload_json: { class_id: CLASS_ID, event_id: EVENT_ID, version: 1, activity_kind: 'ASSESSMENT' },
    },
    studentUser,
    classMap,
  );
  assert.equal(examDeadline.category, 'EXAM');
  assert.equal(examDeadline.targetHref, `/student/classes/${CLASS_ID}/assessments`);

  const teacherCalendar = decodeCoreNotification(
    {
      id: 'notif-teacher-cal',
      type: 'CALENDAR_DEADLINE_CHANGED',
      read: true,
      created_at: '2026-11-24T03:05:00Z',
      payload_json: { class_id: CLASS_ID, event_id: EVENT_ID, version: 1, activity_kind: 'ASSESSMENT' },
    },
    teacherUser,
    classMap,
  );
  assert.equal(teacherCalendar.targetHref, `/teacher/classes/${CLASS_ID}/calendar`);
});

test('formatNotificationEventWindow and notification event bus behave deterministically', () => {
  const windowStr = formatNotificationEventWindow('2026-11-25T00:00:00Z', '2026-11-25T01:30:00Z');
  assert.match(windowStr, /07:00 – 08:30/);

  const received: string[] = [];
  const off = onNotificationStateChanged(event => {
    received.push(`${event.source}:${event.readId ?? 'all'}`);
  });
  emitNotificationStateChanged({ source: 'bell', readId: 'n-1' });
  off();
  emitNotificationStateChanged({ source: 'center', markAll: true });
  assert.deepEqual(received, ['bell:n-1']);
});
