import { useTranslation } from 'react-i18next';
import { PowerOff } from 'lucide-react';

/** Shown when a route's module is switched off in the admin Module Control
 * Center. Semantic tokens only (no legacy hue classnames). */
export function ModuleDisabledPage() {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col items-center justify-center h-full min-h-[50vh] gap-3 text-center px-6">
      <PowerOff size={36} className="text-content-3" />
      <h2 className="text-lg font-semibold text-content">
        {t('moduleDisabled.title', 'Feature Unavailable')}
      </h2>
      <p className="text-sm text-content-2 max-w-md">
        {t('moduleDisabled.message', 'This feature is currently disabled by the administrator.')}
      </p>
    </div>
  );
}
