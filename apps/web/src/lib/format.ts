const ISO_DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const ISO_DATETIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$/;

const VN_DATE_PARTS = new Intl.DateTimeFormat('vi-VN', {
  timeZone: 'Asia/Ho_Chi_Minh',
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

/**
 * Derive 1-2 uppercase letter initials for an actor avatar, ignoring punctuation and numeric suffixes
 * such as `"Admin - 01"` or `"Giáo viên - 01"`.
 */
export function actorInitials(name?: string | null, fallback = 'L'): string {
  const cleaned = (name ?? '').trim();
  if (!cleaned) return fallback;
  const words = cleaned.match(/\p{L}+/gu) ?? [];
  if (words.length >= 2) {
    return words
      .slice(-2)
      .map((word) => word.charAt(0).toLocaleUpperCase('vi-VN'))
      .join('');
  }
  if (words.length === 1) {
    return words[0].slice(0, Math.min(2, words[0].length)).toLocaleUpperCase('vi-VN');
  }
  const alnum = cleaned.match(/[\p{L}\p{N}]+/gu)?.join('') ?? '';
  return alnum ? alnum.slice(0, 2).toLocaleUpperCase('vi-VN') : fallback;
}

/**
 * Format ISO date/datetime strings into `Asia/Ho_Chi_Minh` (`dd/MM/yyyy` or `dd/MM/yyyy HH:mm`).
 * Non-ISO display strings (such as relative labels) are returned untouched.
 */
export function formatDateTime(value?: string | null): string {
  if (!value) return '';
  const trimmed = value.trim();
  if (!trimmed) return '';
  if (ISO_DATE_ONLY.test(trimmed)) {
    const [year, month, day] = trimmed.split('-');
    return `${day}/${month}/${year}`;
  }
  if (!ISO_DATETIME.test(trimmed)) return value;
  const parsed = new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) return value;
  const map: Record<string, string> = {};
  for (const part of VN_DATE_PARTS.formatToParts(parsed)) {
    if (part.type !== 'literal') map[part.type] = part.value;
  }
  if (!map.day || !map.month || !map.year || !map.hour || !map.minute) return value;
  return `${map.day}/${map.month}/${map.year} ${map.hour}:${map.minute}`;
}
