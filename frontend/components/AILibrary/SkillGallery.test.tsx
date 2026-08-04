/**
 * SkillGallery (B3) — the skill library as cards.
 *   1. A card names the agents using the skill.
 *   2. A skill nobody uses says so, in warn colour, with what to do.
 *   3. Clicking a card opens it.
 *   4. Provenance badge distinguishes built-in from self-authored.
 *   5. Search narrows the grid.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { AILibrarySkill } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, d?: string | Record<string, unknown>, o?: Record<string, unknown>) => {
      const def = typeof d === 'string' ? d : '';
      const vars = (typeof d === 'string' ? o : d) ?? {};
      return def.replace(/\{\{(\w+)\}\}/g, (_m, k) => String(vars[k] ?? ''));
    },
  }),
}));

import { SkillGallery } from './SkillGallery';

const skill = (over: Partial<AILibrarySkill>): AILibrarySkill =>
  ({
    id: 1,
    name: 'Script Outline',
    slug: 'script-outline',
    is_public: true,
    frontmatter_json: {},
    files: [],
    updated_at: '',
    ...over,
  }) as AILibrarySkill;

describe('SkillGallery', () => {
  it('states how many agents use a skill, and names them on the avatars', () => {
    // The trailing ", ".join(names) that used to follow the count was dropped
    // in the §05 pass: at minmax(255px) card width it wrapped to three lines
    // for a popular skill and buried the count. The names live on the avatar
    // squares' title attribute now, which is the mockup's form.
    render(
      <SkillGallery
        skills={[
          skill({
            agents: [
              { slug: 'script_ai', name: 'Script AI' },
              { slug: 'storyboard', name: 'Storyboard' },
            ],
          }),
        ]}
        onOpen={() => {}}
      />,
    );
    const card = screen.getByTestId('skill-card');
    expect(card.textContent).toContain('2 agents using');
    expect(card.textContent).not.toContain('Script AI, Storyboard');
    expect(screen.getByTitle('Script AI')).toBeTruthy();
    expect(screen.getByTitle('Storyboard')).toBeTruthy();
  });

  it('puts an icon before the skill name', () => {
    render(<SkillGallery skills={[skill({ category: 'script' })]} onOpen={() => {}} />);
    expect(screen.getByTestId('skill-card-icon')).toBeTruthy();
  });

  it('never renders the stored emoji icon', () => {
    // `skills.icon` holds an emoji (seeds ship `icon: 🎬`) and emoji are banned
    // from this UI — the card derives its icon from the category instead.
    render(
      <SkillGallery
        skills={[skill({ icon: '🎬', category: 'script' })]}
        onOpen={() => {}}
      />,
    );
    expect(screen.getByTestId('skill-card').textContent).not.toContain('🎬');
  });

  it('keeps the orphan hint as a light inline line, not a boxed banner', () => {
    // It was a full-width bordered warn block, which made the quietest fact on
    // the card its loudest element and blew the card past the mockup height.
    render(<SkillGallery skills={[skill({ agents: [] })]} onOpen={() => {}} />);
    const hint = screen.getByTestId('orphan-hint');
    expect(hint.className).not.toContain('border-warn-line');
    expect(hint.className).not.toContain('bg-warn-soft');
    expect(hint.className).toContain('text-warn');
  });

  it('lays the grid out by card width, not by a fixed column count', () => {
    // §05 is repeat(auto-fill, minmax(255px, 1fr)); the old sm:2 / lg:3 gave
    // three over-wide cards on a desktop and wasted the density entirely.
    render(<SkillGallery skills={[skill({})]} onOpen={() => {}} />);
    const grid = screen.getByTestId('skill-grid');
    expect(grid.className).toContain('minmax(255px,1fr)');
    expect(grid.className).not.toContain('lg:grid-cols-3');
  });

  it('warns about a skill no agent uses', () => {
    // An orphan skill is dead weight in every prompt budget conversation;
    // the whole point of the reverse index is making it visible at a glance.
    render(<SkillGallery skills={[skill({ agents: [] })]} onOpen={() => {}} />);
    expect(screen.getByTestId('orphan-hint').textContent).toContain(
      'No agent uses this',
    );
  });

  it('treats a missing agents field the same as an empty one', () => {
    // Older backends (and any endpoint that forgets to populate it) send
    // undefined; silently rendering "0 agents using" without the warning
    // would hide exactly the case we care about.
    render(<SkillGallery skills={[skill({})]} onOpen={() => {}} />);
    expect(screen.getByTestId('orphan-hint')).toBeTruthy();
  });

  it('opens the skill when its card is clicked', () => {
    const onOpen = vi.fn();
    render(<SkillGallery skills={[skill({ agents: [] })]} onOpen={onOpen} />);
    fireEvent.click(screen.getByTestId('skill-card'));
    expect(onOpen).toHaveBeenCalledWith('script-outline');
  });

  it('distinguishes built-in from self-authored skills', () => {
    render(
      <SkillGallery
        skills={[
          skill({ id: 1, slug: 'builtin', is_public: true }),
          skill({ id: 2, slug: 'mine', name: 'Mine', is_public: false }),
        ]}
        onOpen={() => {}}
      />,
    );
    const cards = screen.getAllByTestId('skill-card');
    expect(cards[0].textContent).toContain('Built-in');
    expect(cards[1].textContent).toContain('Custom');
  });

  it('filters by search text', () => {
    render(
      <SkillGallery
        skills={[
          skill({ id: 1, slug: 'script-outline', name: 'Script Outline' }),
          skill({ id: 2, slug: 'translate-copy', name: 'Translate Copy' }),
        ]}
        onOpen={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText('Search skills...'), {
      target: { value: 'transl' },
    });
    expect(screen.getAllByTestId('skill-card')).toHaveLength(1);
  });
});
