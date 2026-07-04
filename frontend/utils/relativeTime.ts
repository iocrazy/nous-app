import { formatDateShort } from './formatDate';

type TranslateFn = (key: string, options?: Record<string, unknown>) => string;

/**
 * Format an ISO-8601 timestamp as a localized, coarse relative-time string
 * (just now / Xm ago / Xh ago / Xd ago), falling back to a localized short
 * date once the timestamp is 7+ days old.
 *
 * Single source of truth for the Projects surface — was previously
 * duplicated (and hardcoded to English) in ProjectCard.tsx and
 * ProjectsListView.tsx.
 */
export function formatRelativeTime(dateStr: string, t: TranslateFn): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMinutes = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMinutes / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMinutes < 1) return t('common.time.justNow');
  if (diffMinutes < 60) return t('common.time.minutesAgo', { count: diffMinutes });
  if (diffHours < 24) return t('common.time.hoursAgo', { count: diffHours });
  if (diffDays < 7) return t('common.time.daysAgo', { count: diffDays });
  return formatDateShort(dateStr);
}
