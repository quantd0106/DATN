import test from 'node:test';
import assert from 'node:assert/strict';
import { actorInitials, formatDateTime } from '../src/lib/format';

test('actorInitials ignores separators and numeric suffixes', () => {
  assert.equal(actorInitials('Admin - 01'), 'AD');
  assert.equal(actorInitials('Giáo viên - 01'), 'GV');
  assert.equal(actorInitials('Học sinh - 01'), 'HS');
  assert.equal(actorInitials('Nguyễn Văn An'), 'VA');
  assert.equal(actorInitials('hs01'), 'HS');
  assert.equal(actorInitials('L'), 'L');
  assert.equal(actorInitials('---', 'NV'), 'NV');
  assert.equal(actorInitials(null, 'L'), 'L');
});

test('formatDateTime converts ISO timestamps to Asia/Ho_Chi_Minh and preserves non-ISO strings', () => {
  assert.equal(formatDateTime('2026-04-02T14:30:00Z'), '02/04/2026 21:30');
  assert.equal(formatDateTime('2026-04-02'), '02/04/2026');
  assert.equal(formatDateTime('5 phút trước'), '5 phút trước');
  assert.equal(formatDateTime('Mặc định hệ thống'), 'Mặc định hệ thống');
  assert.equal(formatDateTime(null), '');
});
