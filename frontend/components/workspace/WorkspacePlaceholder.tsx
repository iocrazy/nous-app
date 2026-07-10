/**
 * WorkspacePlaceholder — shared "coming soon" panel for every workspace
 * sidebar module that isn't wired to real content yet (PR-10b Wave 1 only
 * ships Overview; Episodes mgmt / Entities / Files browser land in Wave 2).
 * Acceptable to ship flag-dark — see epic workflow feedback note.
 */

import { useTranslation } from 'react-i18next';
import type { WorkspaceModule } from './workspaceModules';

interface WorkspacePlaceholderProps {
  module: WorkspaceModule;
}

export function WorkspacePlaceholder({ module }: WorkspacePlaceholderProps) {
  const { t } = useTranslation();
  return (
    <div
      data-testid={`workspace-placeholder-${module}`}
      className="flex flex-col items-center justify-center h-64 gap-2 text-center"
    >
      <p className="text-sm font-medium text-ink-300">
        {t(`projects.workspace.modules.${module}`, module)}
      </p>
      <p className="text-xs text-ink-600">{t('projects.workspace.comingSoon')}</p>
    </div>
  );
}

export default WorkspacePlaceholder;
