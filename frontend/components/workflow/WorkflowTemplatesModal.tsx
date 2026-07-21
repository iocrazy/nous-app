import React, { useEffect } from 'react';
import { GitBranch, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { WorkflowTemplateEditor } from './WorkflowTemplateEditor';

interface WorkflowTemplatesModalProps {
  isOpen: boolean;
  onClose: () => void;
  teamId: string;
}

/**
 * Workflow Templates as a settings-style dialog (owner walkthrough
 * 2026-07-20): the sidebar entry opens this modal instead of swapping the
 * projects list out of the main pane. Shell mirrors SettingsModal — fixed
 * backdrop, centered 82vh panel, Escape / backdrop / X to close — with the
 * team's WorkflowTemplateEditor as the sole content.
 */
export const WorkflowTemplatesModal: React.FC<WorkflowTemplatesModalProps> = ({
  isOpen,
  onClose,
  teamId,
}) => {
  const { t } = useTranslation();

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (isOpen) {
      document.addEventListener('keydown', handleEsc);
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.removeEventListener('keydown', handleEsc);
      document.body.style.overflow = '';
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      data-testid="workflow-templates-modal"
    >
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      <div className="relative flex h-dvh w-full flex-col overflow-hidden border border-ink-800 bg-ink-900 shadow-2xl animate-in fade-in zoom-in-95 duration-200 md:mx-4 md:h-[82vh] md:max-w-6xl md:rounded-2xl">
        <header className="flex shrink-0 items-center justify-between border-b border-ink-800 px-6 pb-4 pt-[max(env(safe-area-inset-top),16px)] md:pt-4">
          <div className="flex items-center gap-2.5">
            <GitBranch size={18} className="text-[var(--accent-text)]" />
            <h2 className="text-base font-semibold text-ink-50">
              {t('projects.workflow.templatesEntry')}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-ink-400 transition hover:bg-ink-800 hover:text-ink-200"
            title="Close"
            data-testid="workflow-templates-modal-close"
          >
            <X size={18} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-hidden bg-app-bg p-5">
          <WorkflowTemplateEditor teamId={teamId} />
        </div>
      </div>
    </div>
  );
};
