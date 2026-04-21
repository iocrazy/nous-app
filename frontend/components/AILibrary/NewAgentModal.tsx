// frontend/components/AILibrary/NewAgentModal.tsx
// Modal form for creating a new user-owned agent (Phase 2 PR 2.8a).
// Supports optional fork from an existing agent (preset or custom).

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibraryAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';

interface NewAgentModalProps {
  existingAgents: AILibraryAgent[];
  onClose: () => void;
  onCreated: (slug: string) => void;
  /**
   * Preselect a source agent to fork from. When set, the "Fork from" dropdown
   * opens with this value already selected. Used by AgentEditor's fork button.
   */
  initialForkFrom?: string;
}

const SLUG_PATTERN = /^[a-z0-9_-]+$/;

export const NewAgentModal: React.FC<NewAgentModalProps> = ({
  existingAgents,
  onClose,
  onCreated,
  initialForkFrom,
}) => {
  const { t } = useTranslation();
  const [slug, setSlug] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [forkFrom, setForkFrom] = useState<string>(initialForkFrom ?? ''); // empty = no fork
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const slugIsValid = slug.length > 0 && SLUG_PATTERN.test(slug);
  const canSubmit = slugIsValid && name.trim().length > 0 && !submitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await aiLibraryService.createAgent({
        slug,
        name: name.trim(),
        description: description.trim() || undefined,
        fork_from: forkFrom || undefined,
      });
      onCreated(created.slug);
    } catch (err) {
      console.error('[NewAgentModal] createAgent failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-lg border border-zinc-800 bg-zinc-900 p-6 shadow-xl">
        <h2 className="text-lg font-semibold text-zinc-100">
          {t('aiLibrary.agents.newAgentTitle', 'New Agent')}
        </h2>
        <p className="mt-1 text-xs text-zinc-500">
          {t(
            'aiLibrary.agents.newAgentHint',
            'Create a custom agent. You can start from scratch or fork an existing agent as a starting point.',
          )}
        </p>

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          <div>
            <label className="block text-xs font-medium text-zinc-400">
              {t('aiLibrary.agents.slugLabel', 'Slug')}
            </label>
            <input
              type="text"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="my-custom-agent"
              className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none"
              disabled={submitting}
              required
            />
            {slug && !slugIsValid && (
              <p className="mt-1 text-xs text-red-400">
                {t(
                  'aiLibrary.agents.slugInvalid',
                  'Lowercase letters, digits, dash, underscore only.',
                )}
              </p>
            )}
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400">
              {t('aiLibrary.agents.nameLabel', 'Name')}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Custom Agent"
              className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none"
              disabled={submitting}
              required
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400">
              {t('aiLibrary.agents.descriptionLabel', 'Description')}
              <span className="ml-1 text-zinc-600">
                ({t('common.optional', 'optional')})
              </span>
            </label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none"
              disabled={submitting}
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400">
              {t('aiLibrary.agents.forkFromLabel', 'Fork from')}
              <span className="ml-1 text-zinc-600">
                ({t('common.optional', 'optional')})
              </span>
            </label>
            <select
              value={forkFrom}
              onChange={(e) => setForkFrom(e.target.value)}
              className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none"
              disabled={submitting}
            >
              <option value="">
                {t('aiLibrary.agents.forkFromNone', 'Start from scratch')}
              </option>
              {existingAgents.map((a) => (
                <option key={a.slug} value={a.slug}>
                  {a.name} {a.is_system_preset ? '(preset)' : ''}
                </option>
              ))}
            </select>
            <p className="mt-1 text-xs text-zinc-500">
              {t(
                'aiLibrary.agents.forkFromHint',
                'Copies the identity / soul / instructions + model settings. Skill bindings are NOT copied.',
              )}
            </p>
          </div>

          {error && (
            <div className="rounded-md border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="rounded-md border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:bg-zinc-800"
            >
              {t('common.cancel', 'Cancel')}
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting
                ? t('common.saving', 'Creating...')
                : t('common.create', 'Create')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default NewAgentModal;
