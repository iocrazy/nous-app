/**
 * ProjectCard (Phase B B1 — Stage Ring layout).
 *
 * The enrichment fields (current_stage / members_preview / latest_activity)
 * are all optional: a legacy project renders the base card, an enriched one
 * gains ring + activity + member stack. Both paths are pinned here.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ProjectCard } from './ProjectCard';
import type { Project } from '../types';

// relativeTime pulls in the real i18n instance via formatDate — stub it so
// this suite doesn't need initReactI18next.
vi.mock('../utils/relativeTime', () => ({
  formatRelativeTime: () => '2h ago',
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown> | string) => {
      if (typeof opts === 'object' && opts && 'stage' in opts) return `Entered ${opts.stage}`;
      if (typeof opts === 'string') return opts;
      return key;
    },
  }),
}));

const base: Project = {
  id: '1',
  name: 'Spring Campaign 2026',
  description: 'Short-form ad series',
  owner_id: 'u-owner',
  team_id: null,
  project_type: 'internal',
  project_group: null,
  announcement: null,
  is_starred: false,
  color_label: null,
  archived_at: null,
  file_count: 128,
  created_at: '2026-06-01T00:00:00+00:00',
  updated_at: '2026-07-06T00:00:00+00:00',
};

const noop = () => {};

describe('ProjectCard', () => {
  it('renders the base card when enrichment fields are absent (legacy shape)', () => {
    render(<ProjectCard project={base} onClick={noop} onToggleStar={noop} />);
    expect(screen.getByText('Spring Campaign 2026')).toBeTruthy();
    // No stage → type badge fallback, no ring, no member stack.
    expect(screen.getByText('Internal')).toBeTruthy();
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.queryByTestId('member-stack')).toBeNull();
  });

  it('renders ring, stage chip, activity line and member stack when enriched', () => {
    const project: Project = {
      ...base,
      current_stage: { slug: 'storyboard', name: 'Storyboard', index: 3, total: 6 },
      members_preview: {
        count: 5,
        members: [
          { user_id: 'u1', username: 'heygo' },
          { user_id: 'u2', username: 'lena' },
          { user_id: 'u3', username: '' },
        ],
      },
      latest_activity: {
        kind: 'stage',
        label: 'Storyboard',
        actor: 'heygo',
        at: '2026-07-05T00:00:00+00:00',
        stalled: false,
      },
    };
    render(<ProjectCard project={project} onClick={noop} onToggleStar={noop} />);
    // Segmented ring with accessible stage label + index text.
    expect(screen.getByRole('img', { name: 'Stage 3 of 6: Storyboard' })).toBeTruthy();
    expect(screen.getByText('3/6')).toBeTruthy();
    // Stage chip replaces the type badge.
    expect(screen.getByText('Storyboard')).toBeTruthy();
    expect(screen.queryByText('Internal')).toBeNull();
    // Activity line with actor.
    expect(screen.getByText(/Entered Storyboard · heygo/)).toBeTruthy();
    // Member stack: 3 previews + overflow chip for the remaining 2.
    expect(screen.getByTestId('member-stack')).toBeTruthy();
    expect(screen.getByText('HE')).toBeTruthy();
    expect(screen.getByText('+2')).toBeTruthy();
  });

  it('stalled activity renders amber dot instead of emerald', () => {
    const project: Project = {
      ...base,
      latest_activity: {
        kind: 'stage',
        label: 'Review',
        at: '2026-07-01T00:00:00',
        stalled: true,
      },
    };
    const { container } = render(<ProjectCard project={project} onClick={noop} onToggleStar={noop} />);
    // Stall dot/label use the `--stall` theme token (D5), not a hardcoded
    // amber utility — match the arbitrary-value class substring instead.
    expect(container.querySelector('[class*="var(--stall)"]')).not.toBeNull();
    expect(container.querySelector('.bg-emerald-400')).toBeNull();
  });

  it('file activity renders the file label and relative time', () => {
    const project: Project = {
      ...base,
      latest_activity: {
        kind: 'file',
        actor: 'HG',
        at: '2026-07-08T00:00:00',
        stalled: false,
      },
    };
    render(<ProjectCard project={project} onClick={noop} onToggleStar={noop} />);
    expect(screen.getByText(/projects\.card\.activityFile/)).toBeTruthy();
    expect(screen.getByText(/HG/)).toBeTruthy();
    expect(screen.getByText(/2h ago/)).toBeTruthy();
    // Emerald dot (not stalled).
    const { container } = render(<ProjectCard project={project} onClick={noop} onToggleStar={noop} />);
    expect(container.querySelector('.bg-emerald-400')).not.toBeNull();
  });

  it('archived projects dim, show the archived chip, and render a muted check ring', () => {
    const project: Project = {
      ...base,
      archived_at: '2026-07-01T00:00:00+00:00',
      current_stage: { slug: 'delivery', name: 'Delivery', index: 6, total: 6 },
    };
    render(<ProjectCard project={project} onClick={noop} onToggleStar={noop} />);
    expect(screen.getByText('projects.card.archived')).toBeTruthy();
    // Archived ring takes precedence over the stage segments: a full muted
    // circle with a check mark, not the segmented "index/total" ring.
    expect(screen.getByTestId('stage-ring-archived')).toBeTruthy();
    expect(screen.getByRole('img', { name: 'Archived' })).toBeTruthy();
    expect(screen.queryByText('6/6')).toBeNull();
  });
});
