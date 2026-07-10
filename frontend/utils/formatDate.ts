import i18n from '../i18n';

/**
 * Format a date string according to the current i18n language.
 * - zh: "2026-02-15 14:30"
 * - en: "Feb 15, 2026 2:30 PM"
 */
export function formatDateLocalized(dateStr: string | null | undefined): string {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  const lang = i18n.language || 'en';

  if (lang.startsWith('zh')) {
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  }

  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

/**
 * Format date without time (short format).
 * - zh: "2026-02-15"
 * - en: "Feb 15, 2026"
 */
export function formatDateShort(dateStr: string | null | undefined): string {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  const lang = i18n.language || 'en';

  if (lang.startsWith('zh')) {
    return date.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    });
  }

  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

/**
 * Format a date-ONLY string ("YYYY-MM-DD") without time-zone drift.
 * `new Date('YYYY-MM-DD')` parses as UTC midnight, so rendering it via
 * toLocaleDateString shifts the calendar day on UTC-negative machines
 * (go-live catch: notes created "today" grouped under yesterday on a
 * UTC-7 box). Date-only strings are calendar dates — parse the parts as a
 * LOCAL date so the same calendar day renders everywhere.
 */
export function formatDateOnlyShort(dateStr: string | null | undefined): string {
  if (!dateStr) return '';
  const [y, m, d] = dateStr.split('-').map(Number);
  if (!y || !m || !d) return formatDateShort(dateStr);
  const date = new Date(y, m - 1, d);
  const lang = i18n.language || 'en';
  if (lang.startsWith('zh')) {
    return date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
  }
  return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}
