import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  inferCalendarEventCategory,
  parseCalendarEventSchedule,
  parseLearningActivityRef,
  spansMultipleLocalDays,
} from '../src/lib/calendar';
import { decodeCalendarEvent } from '../src/lib/data/live-core';

test('calendar event timestamps follow the entered date and time range', () => {
  const schedule = parseCalendarEventSchedule('2026-10-25', '14:00 – 15:30');
  assert.ok(schedule);
  const start = new Date(schedule.start);
  const end = new Date(schedule.end);
  assert.equal(start.getFullYear(), 2026);
  assert.equal(start.getMonth(), 9);
  assert.equal(start.getDate(), 25);
  assert.equal(start.getHours(), 14);
  assert.equal(start.getMinutes(), 0);
  assert.equal(end.getHours(), 15);
  assert.equal(end.getMinutes(), 30);
});

test('calendar event schedule rejects malformed, invalid, and reversed ranges', () => {
  assert.equal(parseCalendarEventSchedule('2026-02-30', '08:00 – 09:00'), null);
  assert.equal(parseCalendarEventSchedule('2026-10-25', '24:00 – 25:00'), null);
  assert.equal(parseCalendarEventSchedule('2026-10-25', '14:00 – 13:00'), null);
  assert.equal(parseCalendarEventSchedule('2026-10-25', 'after lunch'), null);
});

test('multi-day calendar events are distinguished from same-day schedules', () => {
  assert.equal(spansMultipleLocalDays('2026-10-29T09:00:00+07:00', '2026-10-30T10:00:00+07:00'), true);
  assert.equal(spansMultipleLocalDays('2026-10-29T09:00:00+07:00', '2026-10-29T10:00:00+07:00'), false);
  assert.equal(spansMultipleLocalDays('invalid', 'invalid'), false);
});

test('parseLearningActivityRef and inferCalendarEventCategory distinguish exam, assignment, other, and class events', () => {
  const activityId = 'a640270d-5acb-49b6-bc78-5bdf46b60bfb';
  assert.equal(parseLearningActivityRef(`learning:activity:${activityId}`), activityId);
  assert.equal(parseLearningActivityRef('other:ref'), null);

  assert.equal(
    inferCalendarEventCategory({ origin: 'LEARNING', title: 'Anything', learningKind: 'ASSESSMENT' }),
    'EXAM',
  );
  assert.equal(
    inferCalendarEventCategory({ origin: 'LEARNING', title: 'Anything', learningKind: 'ASSIGNMENT' }),
    'ASSIGNMENT',
  );
  assert.equal(
    inferCalendarEventCategory({ origin: 'LEARNING', title: 'kiểm tra thường xuyên 25/11' }),
    'EXAM',
  );
  assert.equal(
    inferCalendarEventCategory({ origin: 'LEARNING', title: 'Nộp bài tuần 6' }),
    'ASSIGNMENT',
  );
  assert.equal(inferCalendarEventCategory({ origin: 'CORE', title: 'Kiểm tra' }), 'EXAM');
  assert.equal(
    inferCalendarEventCategory({ origin: 'CORE', title: 'Bài tập đại số tuần 8' }),
    'ASSIGNMENT',
  );
  assert.equal(
    inferCalendarEventCategory({ origin: 'CORE', title: 'Hoạt động ngoại khóa STEM' }),
    'OTHER',
  );
  assert.equal(
    inferCalendarEventCategory({ origin: 'CORE', title: 'Toán 10: Chữa bài kiểm tra' }),
    'CLASS',
  );
  assert.equal(
    inferCalendarEventCategory({ origin: 'CORE', title: 'Ôn tập hàm số bậc hai' }),
    'CLASS',
  );
});

test('decodeCalendarEvent preserves externalRef and classifies live CORE and LEARNING events', () => {
  const coreExam = decodeCalendarEvent(
    {
      id: '176a1f03-6c9e-4422-91fd-18f32a537319',
      class_id: '4e9814e9-6c48-45ff-bc52-56b92ea4bc64',
      title: 'Kiểm tra',
      description: null,
      starts_at: '2026-11-24T00:00:00Z',
      ends_at: '2026-11-24T02:15:00Z',
      origin: 'CORE',
      external_ref: null,
    },
    'Toán 9A',
  );
  assert.equal(coreExam.category, 'EXAM');
  assert.equal(coreExam.readOnly, false);
  assert.equal(coreExam.externalRef, undefined);

  const learningExam = decodeCalendarEvent(
    {
      id: '8e2921d9-14f4-4ee2-bb21-a981b60ac454',
      class_id: '4e9814e9-6c48-45ff-bc52-56b92ea4bc64',
      title: 'kiểm tra thường xuyên 25/11',
      description: null,
      starts_at: '2026-11-24T17:15:00Z',
      ends_at: '2026-11-24T17:45:00Z',
      origin: 'LEARNING',
      external_ref: 'learning:activity:a640270d-5acb-49b6-bc78-5bdf46b60bfb',
    },
    'Toán 9A',
  );
  assert.equal(learningExam.category, 'EXAM');
  assert.equal(learningExam.readOnly, true);
  assert.equal(learningExam.externalRef, 'learning:activity:a640270d-5acb-49b6-bc78-5bdf46b60bfb');
});
