// frontend/components/AILibrary/SkillsTab.tsx
// Skills tab — read-only card grid listing all skills.
// Task 17 will add the full editor (skill file CRUD).

import React, { useEffect, useState } from 'react';
import type { AILibrarySkill } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';

export const SkillsTab: React.FC = () => {
  const [skills, setSkills] = useState<AILibrarySkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    aiLibraryService
      .listSkills()
      .then((list) => {
        if (cancelled) return;
        setSkills(list);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[SkillsTab] listSkills failed:', err);
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
        {skills.map((s) => (
          <div
            key={s.slug ?? s.id}
            className="rounded-xl border border-zinc-800 bg-zinc-900 p-4 hover:border-zinc-700 transition-colors"
          >
            <div className="mb-2 text-2xl leading-none">{s.icon ?? '🧩'}</div>
            <div className="font-semibold text-zinc-100 truncate">{s.name}</div>
            {s.category && (
              <div className="mt-1 text-xs text-zinc-500 truncate">{s.category}</div>
            )}
            {s.description && (
              <p className="mt-2 text-sm text-zinc-400 line-clamp-3">{s.description}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

export default SkillsTab;
