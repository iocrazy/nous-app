import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Lightbulb, Library, Plus, X } from 'lucide-react';
import type { Topic, TopicStatus } from '../../types';
import {
  createTopic,
  deleteTopic,
  fetchTopics,
  updateTopic,
  type TopicCreatePayload,
} from '../../services/ideationService';
import { getResourceCoverUrl } from '../../services/resourceService';
import { useToast } from '../Toast';
import ResourcePicker from '../chat/ResourcePicker';
import InspirationRefPicker, { type InspirationRef } from './InspirationRefPicker';
import { TopicCard } from './TopicCard';

interface IdeationBoardProps {
  teamId: string;
  /** Open the create-project modal prefilled from a topic (name + topic_id). */
  onCreateProjectFromTopic: (topic: Topic) => void;
}

// All + one tab per status (spec §1). 'all' is a client-side union.
const TABS: readonly (TopicStatus | 'all')[] = [
  'all',
  'candidate',
  'shortlisted',
  'produced',
  'archived',
] as const;

export function IdeationBoard({ teamId, onCreateProjectFromTopic }: IdeationBoardProps) {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [topics, setTopics] = useState<Topic[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<TopicStatus | 'all'>('all');
  const [resourceOpen, setResourceOpen] = useState(false);
  const [inspirationOpen, setInspirationOpen] = useState(false);
  const [newTopicOpen, setNewTopicOpen] = useState(false);
  const [newTitle, setNewTitle] = useState('');

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await fetchTopics(teamId);
      setTopics(rows);
    } catch (err) {
      console.error('Failed to load topics:', err);
      addToast(t('projects.ideation.loadError'), 'error');
    } finally {
      setLoading(false);
    }
  }, [teamId, addToast, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: topics.length };
    for (const status of ['candidate', 'shortlisted', 'produced', 'archived']) {
      c[status] = topics.filter((tp) => tp.status === status).length;
    }
    return c;
  }, [topics]);

  const visible = useMemo(
    () => (tab === 'all' ? topics : topics.filter((tp) => tp.status === tab)),
    [topics, tab],
  );

  // ── Create paths ────────────────────────────────────────────────────────

  const addTopic = useCallback(
    async (payload: TopicCreatePayload) => {
      try {
        const created = await createTopic(teamId, payload);
        // Optimistic prepend (list is newest-first).
        setTopics((prev) => [created, ...prev]);
      } catch (err) {
        console.error('Failed to create topic:', err);
        addToast(t('projects.ideation.createError'), 'error');
      }
    },
    [teamId, addToast, t],
  );

  const handleInspirationSelect = useCallback(
    (ref: InspirationRef) => {
      void addTopic({
        title: ref.title,
        excerpt: ref.excerpt ?? undefined,
        cover_url: ref.cover_url ?? undefined,
        note_id: ref.note_id,
        inspiration_topic_id: ref.inspiration_topic_id,
      });
    },
    [addTopic],
  );

  const handleResourceSelect = useCallback(
    (item: import('../../types').ResourceItem) => {
      const resourceId = item.resource?.id ?? item.resource_id;
      const isVisual =
        item.resource?.mime_type?.startsWith('image/') ||
        item.resource?.mime_type?.startsWith('video/') ||
        !!item.resource?.media_id;
      void addTopic({
        title: item.resource?.filename ?? t('projects.ideation.untitledTopic'),
        resource_id: resourceId,
        media_id: item.resource?.media_id ?? undefined,
        cover_url: isVisual ? getResourceCoverUrl(String(resourceId)) : undefined,
      });
    },
    [addTopic, t],
  );

  const handleNewTopic = useCallback(async () => {
    const title = newTitle.trim();
    if (!title) return;
    await addTopic({ title });
    setNewTitle('');
    setNewTopicOpen(false);
  }, [newTitle, addTopic]);

  // ── Card actions ────────────────────────────────────────────────────────

  const patchStatus = useCallback(
    async (topic: Topic, status: TopicStatus) => {
      // Optimistic; revert on failure.
      setTopics((prev) =>
        prev.map((tp) => (tp.id === topic.id ? { ...tp, status } : tp)),
      );
      try {
        await updateTopic(topic.id, { status });
      } catch (err) {
        console.error('Failed to update topic status:', err);
        addToast(t('projects.ideation.updateError'), 'error');
        setTopics((prev) =>
          prev.map((tp) => (tp.id === topic.id ? { ...tp, status: topic.status } : tp)),
        );
      }
    },
    [addToast, t],
  );

  const handleArchive = useCallback(
    async (topic: Topic) => {
      // Card is removed from non-archived tabs; keep the row so Archived shows it.
      await patchStatus(topic, 'archived');
    },
    [patchStatus],
  );

  return (
    <div data-testid="ideation-board">
      {/* Header + toolbar */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-ink-100">
          <Lightbulb size={18} className="text-[var(--accent-text)]" />
          {t('projects.ideation.title')}
        </h2>
        <div className="flex items-center gap-2">
          <button
            data-testid="ideation-add-inspiration"
            onClick={() => setInspirationOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-md border border-ink-800 px-3 py-1.5 text-[13px] text-ink-300 transition-colors hover:border-ink-700 hover:text-ink-100"
          >
            <Lightbulb size={14} />
            {t('projects.ideation.fromInspiration')}
          </button>
          <button
            data-testid="ideation-add-library"
            onClick={() => setResourceOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-md border border-ink-800 px-3 py-1.5 text-[13px] text-ink-300 transition-colors hover:border-ink-700 hover:text-ink-100"
          >
            <Library size={14} />
            {t('projects.ideation.fromLibrary')}
          </button>
          <button
            data-testid="ideation-new-topic"
            onClick={() => setNewTopicOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-md bg-[var(--accent-soft)] px-3 py-1.5 text-[13px] font-medium text-[var(--accent-text)] transition-colors hover:brightness-110"
          >
            <Plus size={14} />
            {t('projects.ideation.newTopic')}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="mb-4 flex flex-wrap gap-1 border-b border-ink-800">
        {TABS.map((key) => (
          <button
            key={key}
            data-testid={`ideation-tab-${key}`}
            onClick={() => setTab(key)}
            className={`-mb-px border-b-2 px-3 py-2 text-[13px] transition-colors ${
              tab === key
                ? 'border-[var(--accent-border)] text-ink-100'
                : 'border-transparent text-ink-500 hover:text-ink-300'
            }`}
          >
            {t(`projects.ideation.tab.${key}`)}
            <span className="ml-1.5 text-xs text-ink-600">{counts[key] ?? 0}</span>
          </button>
        ))}
      </div>

      {/* Grid */}
      {loading ? (
        <div className="py-16 text-center text-sm text-ink-500">
          {t('common.loading', 'Loading...')}
        </div>
      ) : visible.length === 0 ? (
        <div
          data-testid="ideation-empty"
          className="rounded-xl border border-dashed border-ink-800 py-16 text-center"
        >
          <Lightbulb size={28} className="mx-auto mb-3 text-ink-700" />
          <p className="text-sm text-ink-400">{t('projects.ideation.empty')}</p>
          <p className="mt-1 text-xs text-ink-600">{t('projects.ideation.emptyHint')}</p>
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
          {visible.map((topic) => (
            <TopicCard
              key={topic.id}
              topic={topic}
              onShortlist={(tp) => patchStatus(tp, 'shortlisted')}
              onCreateProject={onCreateProjectFromTopic}
              onArchive={handleArchive}
            />
          ))}
        </div>
      )}

      {/* Pickers */}
      <ResourcePicker
        open={resourceOpen}
        teamId={teamId}
        onClose={() => setResourceOpen(false)}
        onSelect={handleResourceSelect}
      />
      <InspirationRefPicker
        open={inspirationOpen}
        onClose={() => setInspirationOpen(false)}
        onSelect={handleInspirationSelect}
      />

      {/* New topic (blank hand-written) */}
      {newTopicOpen && (
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-black/60 backdrop-blur-[2px]"
          onClick={(e) => e.target === e.currentTarget && setNewTopicOpen(false)}
        >
          <div className="w-[420px] rounded-2xl border border-ink-800 bg-ink-900 p-5 shadow-2xl">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-ink-100">
                {t('projects.ideation.newTopic')}
              </h3>
              <button
                onClick={() => setNewTopicOpen(false)}
                className="rounded-md p-1 text-ink-400 hover:bg-ink-800 hover:text-ink-100"
                aria-label={t('common.cancel')}
              >
                <X size={16} />
              </button>
            </div>
            <input
              data-testid="ideation-new-topic-input"
              autoFocus
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value.slice(0, 300))}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.nativeEvent.isComposing) handleNewTopic();
              }}
              placeholder={t('projects.ideation.newTopicPlaceholder')}
              className="w-full rounded-xl border border-ink-700 bg-ink-800 px-4 py-3 text-sm text-ink-50 placeholder-ink-500 outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setNewTopicOpen(false)}
                className="rounded-lg bg-ink-800 px-4 py-2 text-sm text-ink-300 hover:bg-ink-700"
              >
                {t('common.cancel')}
              </button>
              <button
                data-testid="ideation-new-topic-submit"
                onClick={handleNewTopic}
                disabled={!newTitle.trim()}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:bg-indigo-600/50"
              >
                {t('common.create', 'Create')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
