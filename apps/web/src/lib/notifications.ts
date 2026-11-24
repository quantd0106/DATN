import type { NotificationCategory, NotificationItem } from './domain';

export const NOTIFICATION_CATEGORIES: readonly NotificationCategory[] = [
  'CALENDAR',
  'PRACTICE',
  'ASSIGNMENT',
  'EXAM',
  'DISCUSSION',
  'MATERIAL',
  'CLASS',
  'SYSTEM',
];

export const PRIMARY_NOTIFICATION_CATEGORIES: readonly NotificationCategory[] = [
  'CALENDAR',
  'PRACTICE',
  'ASSIGNMENT',
  'EXAM',
  'DISCUSSION',
];

export function getNotificationCategoryLabel(category: NotificationCategory, customLabel?: string): string {
  if (customLabel) return customLabel;
  switch (category) {
    case 'CALENDAR':
      return 'Lịch học';
    case 'PRACTICE':
      return 'Luyện tập';
    case 'ASSIGNMENT':
      return 'Bài tập';
    case 'EXAM':
      return 'Kiểm tra';
    case 'DISCUSSION':
      return 'Thảo luận';
    case 'MATERIAL':
      return 'Tài liệu học tập';
    case 'CLASS':
      return 'Lớp học';
    case 'SYSTEM':
      return 'Hệ thống';
    default:
      return 'Thông báo';
  }
}

export function getNotificationCategoryShortLabel(category: NotificationCategory): string {
  if (category === 'MATERIAL') return 'Tài liệu';
  return getNotificationCategoryLabel(category);
}

export function getNotificationCategoryBadgeClass(category: NotificationCategory): string {
  switch (category) {
    case 'CALENDAR':
      return 'lumi-notification-badge-calendar';
    case 'PRACTICE':
      return 'lumi-notification-badge-practice';
    case 'ASSIGNMENT':
      return 'lumi-notification-badge-assignment';
    case 'EXAM':
      return 'lumi-notification-badge-exam';
    case 'DISCUSSION':
      return 'lumi-notification-badge-discussion';
    case 'MATERIAL':
      return 'lumi-notification-badge-material';
    case 'CLASS':
      return 'lumi-notification-badge-class';
    case 'SYSTEM':
      return 'lumi-notification-badge-system';
    default:
      return 'lumi-notification-badge-default';
  }
}

export function getNotificationCategoryIconName(category: NotificationCategory): string {
  switch (category) {
    case 'CALENDAR':
      return 'calendar';
    case 'PRACTICE':
      return 'sparkles';
    case 'ASSIGNMENT':
      return 'clipboard';
    case 'EXAM':
      return 'edit';
    case 'DISCUSSION':
      return 'chat';
    case 'MATERIAL':
      return 'file';
    case 'CLASS':
      return 'users';
    case 'SYSTEM':
      return 'shield';
    default:
      return 'bell';
  }
}

export function parseNotificationDate(isoString: string | undefined | null): Date | null {
  if (!isoString || typeof isoString !== 'string') return null;
  const date = new Date(isoString);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function getValidNotificationIso(isoString: string | undefined | null): string | undefined {
  const date = parseNotificationDate(isoString);
  if (!date) return undefined;
  try {
    return date.toISOString();
  } catch {
    return undefined;
  }
}

export function formatNotificationTimeAgo(isoString: string): string {
  const date = parseNotificationDate(isoString);
  if (!date) return 'Chưa có thời gian';

  try {
    const now = Date.now();
    const diffMs = now - date.getTime();
    if (diffMs < 0) return 'Vừa xong';
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return 'Vừa xong';
    if (diffMins < 60) return `${diffMins} phút trước`;
    const diffHours = Math.floor(diffMs / 3600000);
    if (diffHours < 24) {
      const isToday = new Date().toDateString() === date.toDateString();
      const timeStr = date.toLocaleTimeString('vi-VN', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
        timeZone: 'Asia/Ho_Chi_Minh',
      });
      return isToday ? `${timeStr} Hôm nay` : `${diffHours} giờ trước`;
    }
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays === 1) return 'Hôm qua';
    if (diffDays < 7) return `${diffDays} ngày trước`;
    return date.toLocaleDateString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' });
  } catch {
    return 'Chưa có thời gian';
  }
}

export function formatNotificationFullDateTime(isoString: string): string {
  const date = parseNotificationDate(isoString);
  if (!date) return 'Chưa có thời gian';

  try {
    return date.toLocaleString('vi-VN', {
      timeZone: 'Asia/Ho_Chi_Minh',
      hour: '2-digit',
      minute: '2-digit',
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    });
  } catch {
    return 'Chưa có thời gian';
  }
}

export function formatNotificationEventWindow(startsAt?: string, endsAt?: string): string {
  const start = parseNotificationDate(startsAt);
  if (!start) return '';
  const startTime = start.toLocaleTimeString('vi-VN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Ho_Chi_Minh',
  });
  const startDate = start.toLocaleDateString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' });
  const end = parseNotificationDate(endsAt);
  if (!end) return `${startTime} ngày ${startDate}`;
  const endTime = end.toLocaleTimeString('vi-VN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Ho_Chi_Minh',
  });
  const endDate = end.toLocaleDateString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' });
  if (startDate === endDate) {
    return `${startTime} – ${endTime}, ${startDate}`;
  }
  return `${startTime} ${startDate} – ${endTime} ${endDate}`;
}

export function formatUnreadBadgeCount(count: number): string {
  if (!Number.isFinite(count) || count <= 0) return '';
  const normalized = Math.floor(count);
  return normalized > 99 ? '99+' : String(normalized);
}

export function countNotificationsByCategory(
  items: readonly Readonly<NotificationItem>[],
): Record<NotificationCategory, number> {
  const counts: Record<NotificationCategory, number> = {
    CALENDAR: 0,
    PRACTICE: 0,
    ASSIGNMENT: 0,
    EXAM: 0,
    DISCUSSION: 0,
    MATERIAL: 0,
    CLASS: 0,
    SYSTEM: 0,
  };
  for (const item of items) {
    if (item.category in counts) {
      counts[item.category] += 1;
    }
  }
  return counts;
}

export function filterNotifications<T extends Readonly<NotificationItem>>(
  items: readonly T[],
  options: { readFilter?: 'all' | 'unread'; category?: 'ALL' | NotificationCategory },
): T[] {
  const readFilter = options.readFilter ?? 'all';
  const category = options.category ?? 'ALL';
  return items.filter(item => {
    if (readFilter === 'unread' && item.isRead) return false;
    if (category !== 'ALL' && item.category !== category) return false;
    return true;
  });
}
