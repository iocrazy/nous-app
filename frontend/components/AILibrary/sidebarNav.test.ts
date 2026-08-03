import { describe, it, expect } from 'vitest';
import { AI_LIBRARY_NAV, allNavItems, activeNavKey } from './sidebarNav';

describe('AI Library sidebar nav (B0)', () => {
  it('carries exactly the six static entries the redesign allows', () => {
    // The pre-B0 sidebar had ~30 rows because agents and skills were
    // flattened one-per-line. If this count grows, something got flattened
    // back in — that is the regression this test exists to catch.
    expect(allNavItems().map((i) => i.key)).toEqual([
      'library',
      'workforce',
      'sessions',
      'usage',
      'aiCost',
      'memory',
    ]);
  });

  it('has no per-agent or per-skill entry', () => {
    for (const item of allNavItems()) {
      expect(item.path).not.toMatch(/\/agents\/[^/]/);
      expect(item.path).not.toMatch(/\/skills\/[^/]/);
    }
    expect(allNavItems().some((i) => i.key === 'skills')).toBe(false);
  });

  it('activates the library entry only on the index path', () => {
    expect(activeNavKey('/team/7/ai-library')).toBe('library');
    expect(activeNavKey('/team/7/ai-library/')).toBe('library');
  });

  it('leaves the library entry inactive inside a detail view', () => {
    // Agents and skills reached from the gallery cards are NOT the gallery;
    // highlighting the entry there would tell the user they are somewhere
    // they can navigate to.
    expect(activeNavKey('/team/7/ai-library/agents/script_ai')).toBeNull();
    expect(activeNavKey('/team/7/ai-library/skills/script-outline')).toBeNull();
  });

  it('resolves each remaining entry from its own path', () => {
    expect(activeNavKey('/team/7/ai-library/workforce')).toBe('workforce');
    expect(activeNavKey('/team/7/ai-library/sessions/42')).toBe('sessions');
    expect(activeNavKey('/team/7/ai-library/usage')).toBe('usage');
    expect(activeNavKey('/team/7/ai-library/ai-cost')).toBe('aiCost');
    expect(activeNavKey('/team/7/ai-library/memory')).toBe('memory');
  });

  it('groups entries under at most four sections', () => {
    expect(AI_LIBRARY_NAV.map((s) => s.key)).toEqual([
      'library',
      'runtime',
      'chat',
      'insights',
    ]);
  });
});
