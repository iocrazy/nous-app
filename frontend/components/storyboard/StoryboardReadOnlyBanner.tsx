import { useTranslation } from 'react-i18next';

/**
 * Read-only notice for the legacy storyboard workbench (Phase B P3 cutover).
 *
 * Shots now live in the script editor's storyboard view; this standalone
 * canvas is being retired (the data drop is P4). The banner sets expectations
 * while the primary write entry points are disabled at the page level.
 */
export function StoryboardReadOnlyBanner() {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      data-testid="storyboard-readonly-banner"
      className="flex items-center justify-center gap-2 px-4 py-2 text-xs font-medium text-warn-soft bg-amber-500/15 border-b border-amber-500/30"
    >
      {t('storyboard.readOnlyBanner')}
    </div>
  );
}
