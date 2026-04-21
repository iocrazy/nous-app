// frontend/components/AILibrary/SkillsTab.tsx
// Skills tab — clickable card grid. Clicking a card opens the SkillEditor.
// Back from editor triggers a refresh so edits propagate to the grid.

import React, { useEffect, useState } from 'react';
import type { AILibrarySkill } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { SkillEditor } from './SkillEditor';

export const SkillsTab: React.FC = () => {
  const [skills, setSkills] = useState<AILibrarySkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingSlug, setEditingSlug] = useState<string | null>(null);

  const load = (): void => {
    setLoading(true);
    setError(null);
    aiLibraryService
      .listSkills()
      .then((list) => setSkills(list))
      .catch((err) => {
        console.error('[SkillsTab] listSkills failed:', err);
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  if (editingSlug) {
    return (
      <SkillEditor
        slug={editingSlug}
        onBack={() => {
          setEditingSlug(null);
          load();
        }}
      />
    );
  }

  if (loading) {
    return <div className="p-6 text-sm text-zinc-500">Loading skills...</div>;
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
          Failed to load skills: {error}
        </div>
      </div>
    );
  }

  if (skills.length === 0) {
    return <div className="p-6 text-sm text-zinc-500">No skills available.</div>;
  }

  return (
    <div className="p-6">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {skills.map((s) => {
          const key = s.slug ?? String(s.id);
          return (
            <button
              key={key}
              onClick={() => setEditingSlug(s.slug ?? String(s.id))}
              className="rounded-xl border border-zinc-800 bg-zinc-900 p-4 text-left hover:border-zinc-700 hover:bg-zinc-800/60 focus:outline-none focus:ring-2 focus:ring-indigo-500 transition-colors"
            >
              <div className="mb-2 text-2xl leading-none">{s.icon ?? '🧩'}</div>
              <div className="font-semibold text-zinc-100 truncate">{s.name}</div>
              {s.category && (
                <div className="mt-1 text-xs text-zinc-500 truncate">{s.category}</div>
              )}
              {s.description && (
                <p className="mt-2 text-sm text-zinc-400 line-clamp-3">{s.description}</p>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
};

export default SkillsTab;
