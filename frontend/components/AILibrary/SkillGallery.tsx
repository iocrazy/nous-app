// frontend/components/AILibrary/SkillGallery.tsx
// B3 — the skill library as cards (spec 2026-08-02 §B3).
//
// The rail listed skills as filenames, which answered "what is it called"
// and nothing else. The card answers the question people actually open this
// page with: who uses this, and is anything here dead weight.

import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Search } from 'lucide-react';
import type { AILibrarySkill } from '../../types';

interface SkillGalleryProps {
  skills: AILibrarySkill[];
  onOpen: (slug: string) => void;
  /** Rendered in the toolbar — typically the "+ New Skill" button. */
  actions?: React.ReactNode;
  /** Rendered above the toolbar — the shared Agents | Skills tab strip. */
  header?: React.ReactNode;
}

/** Same convention as SkillEditor: public + unowned = built-in. */
const isBuiltIn = (s: AILibrarySkill): boolean =>
  s.is_public && s.team_id == null && s.project_id == null;

const skillKey = (s: AILibrarySkill): string => s.slug ?? String(s.id);

function SkillCard({
  skill,
  onOpen,
}: {
  skill: AILibrarySkill;
  onOpen: (slug: string) => void;
}) {
  const { t } = useTranslation();
  // `undefined` (older payloads) and `[]` mean the same thing here — both
  // are "nothing binds this", and both must show the warning.
  const agents = skill.agents ?? [];
  const scope =
    skill.team_name || skill.team_id != null
      ? t('aiLibrary.scope.team', 'Team')
      : skill.project_name || skill.project_id != null
        ? t('aiLibrary.scope.project', 'Project')
        : t('aiLibrary.scope.private', 'Private');

  return (
    <button
      type="button"
      onClick={() => onOpen(skillKey(skill))}
      data-testid="skill-card"
      className="rounded-xl border border-ink-800/60 bg-ink-900/20 p-3 text-left transition-colors hover:border-ink-700"
    >
      <div className="truncate text-[13px] font-medium text-ink-200">{skill.name}</div>
      {skill.description && (
        <div className="mt-1 line-clamp-2 text-[11px] text-ink-500">
          {skill.description}
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-ink-600">
        <span
          className={`rounded border px-1.5 py-0.5 ${
            isBuiltIn(skill)
              ? 'border-info-line bg-info-soft text-info'
              : 'border-ink-800'
          }`}
        >
          {isBuiltIn(skill)
            ? t('aiLibrary.skills.builtIn', 'Built-in')
            : t('aiLibrary.customAgent', 'Custom')}
        </span>
        <span className="rounded border border-ink-800 px-1.5 py-0.5">{scope}</span>
      </div>

      {agents.length > 0 ? (
        <div className="mt-2 flex items-center gap-1.5" data-testid="used-by">
          <span className="flex -space-x-1">
            {agents.slice(0, 4).map((a) => (
              <span
                key={a.slug}
                title={a.name}
                className="flex h-4 w-4 items-center justify-center rounded-sm border border-ink-800 bg-agent-soft text-[8px] text-agent"
              >
                {a.name.slice(0, 1).toUpperCase()}
              </span>
            ))}
          </span>
          <span className="min-w-0 truncate text-[11px] text-ink-500">
            {t('aiLibrary.skills.usedBy', '{{count}} agents using', {
              count: agents.length,
            })}
            {' · '}
            {agents.map((a) => a.name).join(', ')}
          </span>
        </div>
      ) : (
        <div
          className="mt-2 flex items-start gap-1.5 rounded-md border border-warn-line bg-warn-soft px-2 py-1 text-[11px] text-warn"
          data-testid="orphan-hint"
        >
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <span>
            {t(
              'aiLibrary.skills.orphanHint',
              'No agent uses this — bind it to an agent or archive it',
            )}
          </span>
        </div>
      )}
    </button>
  );
}

export const SkillGallery: React.FC<SkillGalleryProps> = ({
  skills,
  onOpen,
  actions,
  header,
}) => {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter((s) =>
      `${s.name} ${s.slug ?? ''} ${s.description ?? ''}`.toLowerCase().includes(q),
    );
  }, [skills, query]);

  const orphanCount = skills.filter((s) => (s.agents ?? []).length === 0).length;

  return (
    <div className="pt-6 pb-12">
      {header}
      <div className={`flex flex-wrap items-center gap-2 ${header ? 'mt-3' : ''}`}>
        <div className="relative">
          <Search
            size={13}
            className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-ink-600"
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('aiLibrary.skills.search', 'Search skills...')}
            aria-label={t('aiLibrary.skills.search', 'Search skills...')}
            className="w-56 rounded-md border border-ink-800 bg-transparent py-1 pl-7 pr-2 text-[12px] text-ink-200 placeholder:text-ink-600"
          />
        </div>
        {orphanCount > 0 && (
          <span className="rounded-full border border-warn-line bg-warn-soft px-2.5 py-1 text-[11px] text-warn">
            {t('aiLibrary.skills.orphanCount', '{{count}} unused', {
              count: orphanCount,
            })}
          </span>
        )}
        <div className="ml-auto">{actions}</div>
      </div>

      <div className="mt-4 grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
        {visible.map((s) => (
          <SkillCard key={skillKey(s)} skill={s} onOpen={onOpen} />
        ))}
      </div>

      {visible.length === 0 && (
        <div className="mt-4 rounded-lg border border-ink-800/60 px-4 py-8 text-center text-[12px] text-ink-600">
          {skills.length === 0
            ? t('aiLibrary.skills.empty', 'No skills yet. Create your first one.')
            : t('aiLibrary.skills.noneMatch', 'No skills match this search')}
        </div>
      )}
    </div>
  );
};

export default SkillGallery;
