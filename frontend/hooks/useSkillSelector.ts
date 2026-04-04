import { useState, useEffect, useCallback, useMemo } from 'react';
import { Skill } from '../types';
import { fetchSkills } from '../services/skillService';

interface SkillGroup {
  readonly label: string;
  readonly skills: readonly Skill[];
}

interface UseSkillSelectorResult {
  readonly groups: readonly SkillGroup[];
  readonly selectedSkill: Skill | null;
  readonly loading: boolean;
  readonly selectSkill: (skill: Skill | null) => void;
  readonly filterText: string;
  readonly setFilterText: (text: string) => void;
}

export function useSkillSelector(projectId: string): UseSkillSelectorResult {
  const [skills, setSkills] = useState<readonly Skill[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchSkills(projectId)
      .then((data) => {
        if (!cancelled) setSkills(data);
      })
      .catch((err) => console.error('[SkillSelector] fetch failed:', err))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [projectId]);

  const groups = useMemo(() => {
    const lower = filterText.toLowerCase();
    const filtered = filterText
      ? skills.filter(
          (s) =>
            s.name.toLowerCase().includes(lower) ||
            (s.description || '').toLowerCase().includes(lower),
        )
      : skills;

    const project: Skill[] = [];
    const global: Skill[] = [];
    const system: Skill[] = [];

    for (const s of filtered) {
      if (s.team_id === null || s.team_id === undefined) {
        system.push(s);
      } else if (s.project_id) {
        project.push(s);
      } else {
        global.push(s);
      }
    }

    const result: SkillGroup[] = [];
    if (project.length > 0) result.push({ label: '📁 Project', skills: project });
    if (global.length > 0) result.push({ label: '🌐 Global', skills: global });
    if (system.length > 0) result.push({ label: '⚙️ System', skills: system });
    return result;
  }, [skills, filterText]);

  const selectSkill = useCallback((skill: Skill | null) => {
    setSelectedSkill(skill);
  }, []);

  return { groups, selectedSkill, loading, selectSkill, filterText, setFilterText };
}
