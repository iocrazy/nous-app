/**
 * AttachWorkflowModal — attach a workflow template to an EXISTING project
 * that has none yet (M1.x opt-in migration path).
 *
 * This is the answer to "can we migrate the old SOP projects" — instead of a
 * lossy bulk script mapping the legacy 3-stage SOP onto an 11-node template,
 * the user opts a project in, one at a time, and picks the template
 * themselves. Reuses the same template-choice cards
 * (`WorkflowChoiceCard`) and method picker as CreateProjectModal's Workflow
 * block so the two pickers read identically.
 */

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { GitBranch, X } from 'lucide-react';
import { fetchTemplates, attachProjectWorkflow } from '../../services/workflowService';
import { ApiError } from '../../services/apiClient';
import type { ProjectStageNode, WorkflowTemplate } from '../../types';
import { WorkflowChoiceCard } from './WorkflowChoiceCard';

type WorkflowMethod = 'live' | 'ai' | 'hybrid';
const WORKFLOW_METHODS: WorkflowMethod[] = ['live', 'ai', 'hybrid'];

interface AttachWorkflowModalProps {
  projectId: string;
  /** The project's team (personal project → '' — the server resolves that
   * to the owner's own personal team, same convention as CreateProjectModal). */
  teamId: string;
  onClose: () => void;
  onAttached: (nodes: ProjectStageNode[]) => void;
}

export const AttachWorkflowModal: React.FC<AttachWorkflowModalProps> = ({
  projectId,
  teamId,
  onClose,
  onAttached,
}) => {
  const { t } = useTranslation();
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [templateId, setTemplateId] = useState<string>('');
  const [method, setMethod] = useState<WorkflowMethod>('hybrid');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetchTemplates(teamId)
      .then((list) => {
        if (!alive) return;
        setTemplates(list);
        setTemplateId(list.find((tpl) => tpl.is_default)?.id ?? list[0]?.id ?? '');
      })
      .catch((err) => {
        console.error('[AttachWorkflowModal] failed to load templates', err);
        if (alive) setError(t('projects.workflow.attach.loadFailed'));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [teamId, t]);

  const handleSubmit = async () => {
    if (!templateId) return;
    setSubmitting(true);
    setError(null);
    try {
      const nodes = await attachProjectWorkflow(projectId, {
        template_id: templateId,
        method,
      });
      onAttached(nodes);
      onClose();
    } catch (err) {
      console.error('[AttachWorkflowModal] attach failed', err);
      if (err instanceof ApiError && err.status === 409) {
        setError(t('projects.workflow.attach.alreadyHasWorkflow'));
      } else {
        setError(t('projects.workflow.attach.failed'));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      data-testid="attach-workflow-modal"
    >
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      <div className="relative w-full max-w-md mx-4 rounded-2xl border border-ink-800 bg-ink-900 shadow-2xl">
        <div className="flex items-center justify-between p-6 border-b border-ink-800">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-[var(--accent-soft)] rounded-lg">
              <GitBranch size={20} className="text-[var(--accent-text)]" />
            </div>
            <h2 className="text-lg font-semibold text-ink-50">
              {t('projects.workflow.attach.title')}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-ink-400 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        <div className="p-6 space-y-4">
          <p className="text-[13px] text-ink-400">{t('projects.workflow.attach.body')}</p>

          {loading ? (
            <div className="text-[13px] text-ink-500 italic">{t('common.loading')}</div>
          ) : templates.length === 0 ? (
            <div className="text-[13px] text-ink-500 italic">
              {t('projects.workflow.attach.noTemplates')}
            </div>
          ) : (
            <>
              <div className="grid grid-cols-3 gap-2">
                {templates.map((tpl) => (
                  <WorkflowChoiceCard
                    key={tpl.id}
                    title={tpl.name}
                    hint={`${tpl.node_count} nodes`}
                    selected={templateId === tpl.id}
                    onClick={() => setTemplateId(tpl.id)}
                    testId={`attach-workflow-template-${tpl.id}`}
                  />
                ))}
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-medium text-ink-400">
                  {t('projects.workflow.create.method')}
                </label>
                <div className="flex gap-2">
                  {WORKFLOW_METHODS.map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => setMethod(m)}
                      data-testid={`attach-workflow-method-${m}`}
                      className={`flex-1 rounded-lg border px-3 py-2 text-sm transition ${
                        method === m
                          ? 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)]'
                          : 'border-ink-700 text-ink-400 hover:border-ink-600'
                      }`}
                    >
                      {t(
                        m === 'live'
                          ? 'projects.workflow.create.methodLive'
                          : m === 'ai'
                            ? 'projects.workflow.create.methodAI'
                            : 'projects.workflow.create.methodHybrid',
                      )}
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}

          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
              {error}
            </div>
          )}

          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-4 py-3 text-ink-300 bg-ink-800 hover:bg-ink-700 rounded-xl font-medium transition-colors"
            >
              {t('common.cancel')}
            </button>
            <button
              type="button"
              onClick={() => void handleSubmit()}
              disabled={submitting || loading || !templateId}
              data-testid="attach-workflow-submit"
              className="flex-1 px-4 py-3 text-white bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 disabled:cursor-not-allowed rounded-xl font-medium transition-colors"
            >
              {submitting
                ? (t('common.creating') || 'Creating...')
                : t('projects.workflow.attach.submit')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AttachWorkflowModal;
