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
