/**
 * skillIconFor — picks a lucide icon for a skill card (spec §05).
 *
 * The load-bearing rule is the last test: `skills.icon` in the database holds
 * an EMOJI (see backend/seeds/skills/script-outline/SKILL.md `icon: 🎬`), and
 * emoji are banned from this UI. So the resolver must derive its icon from
 * category/name/slug and never touch that column.
 */
import { describe, it, expect } from 'vitest';
import { Film, FileText, PenTool, Search, Send } from 'lucide-react';
import { skillIconFor, DEFAULT_SKILL_ICON } from './skillIcons';

describe('skillIconFor', () => {
  it('maps a storyboard skill to the film icon', () => {
    expect(skillIconFor({ category: 'storyboard', name: 'Film Storyboard' })).toBe(Film);
  });

  it('maps a copywriting skill to the pen icon', () => {
    expect(skillIconFor({ category: null, name: 'Product Copywriting' })).toBe(PenTool);
  });

  it('maps a publishing skill to the send icon', () => {
    expect(skillIconFor({ category: null, name: 'Social Media Post' })).toBe(Send);
  });

  it('falls back to the slug when category and name say nothing', () => {
    expect(skillIconFor({ name: 'Untitled', slug: 'deep-research-brief' })).toBe(Search);
  });

  it('returns the default icon for an unrecognised skill', () => {
    expect(skillIconFor({ name: 'Zzz', slug: 'zzz' })).toBe(DEFAULT_SKILL_ICON);
    expect(DEFAULT_SKILL_ICON).toBe(FileText);
  });

  it('never throws on an empty skill', () => {
    expect(skillIconFor({})).toBe(DEFAULT_SKILL_ICON);
  });

  it('ignores the stored emoji icon column entirely', () => {
    // Rendering skill.icon would put 🎬 on the card. The resolver must pick
    // by category instead, so an emoji in that column can never leak through.
    const withEmoji = { icon: '🎬', category: 'script', name: 'Script Outline' };
    const withoutEmoji = { category: 'script', name: 'Script Outline' };
    expect(skillIconFor(withEmoji)).toBe(skillIconFor(withoutEmoji));
    expect(skillIconFor(withEmoji)).not.toBe(undefined);
  });

  it('is case-insensitive on the category', () => {
    expect(skillIconFor({ category: 'STORYBOARD' })).toBe(Film);
  });
});
