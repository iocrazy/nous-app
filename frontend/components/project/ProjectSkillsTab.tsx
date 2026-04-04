import { useState, useEffect, useCallback } from 'react';
import { Wand2, Plus, Archive } from 'lucide-react';
import { Skill } from '../../types';
import { fetchSkills, deleteSkill, invalidateSkillsCache } from '../../services/skillService';
import { useToast } from '../Toast';

const CATEGORY_TABS = ['all', 'script', 'storyboard', 'copywriting', 'general'] as const;

interface ProjectSkillsTabProps {
  projectId: string;
  onEditSkill?: (skillId: string | null) => void;
}

export function ProjectSkillsTab({ projectId, onEditSkill }: ProjectSkillsTabProps) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeCategory, setActiveCategory] = useState<string>('all');
  const { addToast } = useToast();

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      invalidateSkillsCache();
      const category = activeCategory === 'all' ? undefined : activeCategory;
      const data = await fetchSkills(projectId, category);
      setSkills(data);
    } catch (err) {
      console.error('[ProjectSkillsTab] load failed:', err);
      addToast('Failed to load skills', 'error');
    } finally {
      setLoading(false);
    }
  }, [projectId, activeCategory, addToast]);

  useEffect(() => { loadSkills(); }, [loadSkills]);

  const handleArchive = useCallback(async (skillId: string) => {
    try {
      await deleteSkill(skillId);
      addToast('Skill archived', 'success');
      loadSkills();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to archive skill';
      addToast(msg, 'error');
    }
  }, [addToast, loadSkills]);

  // Separate into groups
  const projectSkills = skills.filter((s) => s.project_id);
  const globalSkills = skills.filter((s) => s.team_id && !s.project_id);
  const systemSkills = skills.filter((s) => !s.team_id);

  if (loading) {
    return (
      <div className="flex flex-wrap gap-5 p-6">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-[140px] w-[280px] animate-pulse rounded-xl bg-zinc-900" />
        ))}
      </div>
    );
  }

  if (skills.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 py-24 text-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-violet-500/15">
          <Wand2 className="h-8 w-8 text-violet-400" />
        </div>
        <div>
          <h3 className="text-lg font-medium text-zinc-100">No Skills Yet</h3>
          <p className="mt-1 text-sm text-zinc-500">
            Create your first skill to standardize your AI outputs
          </p>
        </div>
        <button
          onClick={() => onEditSkill?.(null)}
          className="mt-2 flex items-center gap-2 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-violet-500"
        >
          <Plus className="h-4 w-4" />
          New Skill
        </button>
      </div>
    );
  }

  return (
    <div className="p-6">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-zinc-100">Skills</h2>
          <p className="text-sm text-zinc-500">{skills.length} skills available</p>
        </div>
        <button
          onClick={() => onEditSkill?.(null)}
          className="flex items-center gap-2 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-violet-500"
        >
          <Plus className="h-4 w-4" />
          New Skill
        </button>
      </div>

      {/* Category tabs */}
      <div className="mb-6 flex gap-1 rounded-lg bg-zinc-800/50 p-1">
        {CATEGORY_TABS.map((cat) => (
          <button
            key={cat}
            onClick={() => setActiveCategory(cat)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium capitalize transition-colors ${
              activeCategory === cat
                ? 'bg-zinc-700 text-zinc-100'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            {cat}
          </button>
        ))}
      </div>

      {/* Skill groups */}
      {projectSkills.length > 0 && (
        <SkillGroup
          label="📁 Project Skills"
          skills={projectSkills}
          onEdit={onEditSkill}
          onArchive={handleArchive}
          canEdit
        />
      )}
      {globalSkills.length > 0 && (
        <SkillGroup
          label="🌐 Global Skills"
          skills={globalSkills}
          onEdit={onEditSkill}
          onArchive={handleArchive}
          canEdit
        />
      )}
      {systemSkills.length > 0 && (
        <SkillGroup
          label="⚙️ System Presets"
          skills={systemSkills}
          onEdit={onEditSkill}
          canEdit={false}
        />
      )}
    </div>
  );
}

// ─── Sub-components ──────────────────────────────────────────────────────────

function SkillGroup({
  label,
  skills,
  onEdit,
  onArchive,
  canEdit,
}: {
  label: string;
  skills: Skill[];
  onEdit?: (id: string | null) => void;
  onArchive?: (id: string) => void;
  canEdit: boolean;
}) {
  return (
    <div className="mb-8">
      <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">
        {label}
      </h3>
      <div className="flex flex-wrap gap-5">
        {skills.map((skill) => (
          <SkillCard
            key={skill.id}
            skill={skill}
            onClick={() => onEdit?.(skill.id)}
            onArchive={canEdit ? () => onArchive?.(skill.id) : undefined}
          />
        ))}
      </div>
    </div>
  );
}

function SkillCard({
  skill,
  onClick,
  onArchive,
}: {
  skill: Skill;
  onClick: () => void;
  onArchive?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="group flex w-[280px] flex-col rounded-xl border border-zinc-800/60 bg-zinc-900/50 p-5 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-zinc-600 hover:bg-zinc-800/40 hover:shadow-lg"
    >
      <div className="mb-3 flex items-start justify-between">
        <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-violet-500/15">
          <span className="text-lg">{skill.icon}</span>
        </div>
        {onArchive && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onArchive();
            }}
            className="rounded p-1 text-zinc-600 opacity-0 transition-all hover:bg-zinc-700 hover:text-zinc-300 group-hover:opacity-100"
            aria-label="Archive skill"
          >
            <Archive className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <h4 className="mb-1 truncate text-sm font-medium text-zinc-100">
        {skill.name}
      </h4>
      {skill.category && (
        <span className="mb-2 inline-block self-start rounded-full border border-violet-800/50 bg-violet-900/30 px-2 py-0.5 text-[10px] font-medium text-violet-400">
          {skill.category}
        </span>
      )}
      {skill.description && (
        <p className="line-clamp-2 text-xs leading-relaxed text-zinc-500">
          {skill.description}
        </p>
      )}
    </button>
  );
}
