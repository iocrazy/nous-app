import { useTranslation } from 'react-i18next';
import { ArrowRight, FolderPlus, Archive, Hash, Lightbulb, Library, PenLine } from 'lucide-react';
import type { Topic, TopicSource, TopicStatus } from '../../types';

/** Derive which library a topic references from its id columns (spec §1). */
export function topicSource(topic: Topic): TopicSource {
  if (topic.inspiration_topic_id) return 'topic';
  if (topic.note_id) return 'inspiration';
  if (topic.resource_id || topic.media_id) return 'library';
  return 'blank';
}

const SOURCE_META: Record<
  TopicSource,
  { icon: React.ElementType; labelKey: string }
> = {
  inspiration: { icon: Lightbulb, labelKey: 'projects.ideation.source.inspiration' },
  topic: { icon: Hash, labelKey: 'projects.ideation.source.topic' },
  library: { icon: Library, labelKey: 'projects.ideation.source.library' },
  blank: { icon: PenLine, labelKey: 'projects.ideation.source.blank' },
};

const STATUS_STYLE: Record<TopicStatus, string> = {
  candidate: 'bg-ink-800 text-ink-300',
  shortlisted: 'bg-amber-500/15 text-warn',
  produced: 'bg-emerald-500/15 text-emerald-300',
  archived: 'bg-ink-800/60 text-ink-500',
};

interface TopicCardProps {
  topic: Topic;
  onShortlist: (topic: Topic) => void;
  onCreateProject: (topic: Topic) => void;
  onArchive: (topic: Topic) => void;
}

export function TopicCard({
  topic,
  onShortlist,
  onCreateProject,
  onArchive,
}: TopicCardProps) {
  const { t } = useTranslation();
  const source = topicSource(topic);
  const SourceIcon = SOURCE_META[source].icon;

  return (
    <div
      data-testid="topic-card"
      className="flex flex-col rounded-xl border border-ink-800 bg-ink-900 overflow-hidden transition-colors hover:border-ink-700"
    >
      {/* Cover */}
      <div className="relative aspect-video w-full bg-ink-950 overflow-hidden">
        {topic.cover_url ? (
          <img
            src={topic.cover_url}
            alt={topic.title}
            className="h-full w-full object-cover"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = 'none';
            }}
          />
        ) : (
          <div className="absolute inset-0 grid place-items-center text-ink-700">
            <SourceIcon size={28} />
          </div>
        )}
        {/* Source badge */}
        <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded-full bg-black/55 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-ink-100 backdrop-blur-sm">
          <SourceIcon size={11} />
          {t(SOURCE_META[source].labelKey)}
        </span>
        {/* Status pill */}
        <span
          className={`absolute right-2 top-2 rounded-full px-2 py-0.5 text-[10px] font-medium ${STATUS_STYLE[topic.status]}`}
        >
          {t(`projects.ideation.status.${topic.status}`)}
        </span>
      </div>

      {/* Body */}
      <div className="flex flex-1 flex-col gap-1 p-3">
        <h3 className="line-clamp-2 text-sm font-semibold text-ink-100" title={topic.title}>
          {topic.title}
        </h3>
        {topic.excerpt && (
          <p className="line-clamp-2 text-xs text-ink-500">{topic.excerpt}</p>
        )}

        {/* Actions */}
        <div className="mt-auto flex items-center gap-2 pt-3">
          {topic.status === 'candidate' && (
            <button
              data-testid="topic-shortlist"
              onClick={() => onShortlist(topic)}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-ink-300 transition-colors hover:bg-ink-800 hover:text-ink-100"
            >
              {t('projects.ideation.action.shortlist')}
              <ArrowRight size={12} />
            </button>
          )}
          {topic.status !== 'archived' && (
            <button
              data-testid="topic-create-project"
              onClick={() => onCreateProject(topic)}
              className="inline-flex items-center gap-1 rounded-md bg-[var(--accent-soft)] px-2 py-1 text-xs font-medium text-[var(--accent-text)] transition-colors hover:brightness-110"
            >
              <FolderPlus size={12} />
              {t('projects.ideation.action.createProject')}
            </button>
          )}
          {topic.status !== 'archived' && (
            <button
              data-testid="topic-archive"
              onClick={() => onArchive(topic)}
              title={t('projects.ideation.action.archive')}
              className="ml-auto rounded-md p-1 text-ink-500 transition-colors hover:bg-ink-800 hover:text-ink-300"
            >
              <Archive size={13} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
