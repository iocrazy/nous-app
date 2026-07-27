/**
 * ProjectCard (Phase B B1 — Stage Ring layout).
 *
 * The enrichment fields (workflow_badge / members_preview / latest_activity)
 * are all optional: a legacy project renders the base card, an enriched one
 * gains ring + activity + member stack. The Stage Ring is workflow-driven only
 * (G3 dropped the legacy SOP `current_stage` source — a No-workflow project
 * renders no ring). Both paths are pinned here.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ProjectCard } from './ProjectCard';
import * as svc from '../services/projectsService';
import type { Project, ProjectSuggestionItem } from '../types';

// relativeTime pulls in the real i18n instance via formatDate — stub it so
// this suite doesn't need initReactI18next.
vi.mock('../utils/relativeTime', () => ({
  formatRelativeTime: () => '2h ago',
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown> | string) => {
      if (typeof opts === 'object' && opts && 'stage' in opts) return `Entered ${opts.stage}`;
      if (typeof opts === 'object' && opts && 'count' in opts && opts.count != null) return `${key}:${opts.count}`;
      if (typeof opts === 'string') return opts;
      return key;
    },
  }),
}));

// PR-9 grid suggestion row fires generateMissingFrames via useToast() — the
// pre-existing tests below don't wrap a ToastProvider, so stub the hook
// (harmless for suites that never render a suggestion row).
vi.mock('./Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

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
      workflow_badge: {
        current_node_name: 'Storyboard',
        workflow_total: 6,
        workflow_position: 3,
        agents_active: 0,
      },
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
      workflow_badge: {
        current_node_name: 'Delivery',
        workflow_total: 6,
        workflow_position: 6,
        agents_active: 0,
      },
    };
    render(<ProjectCard project={project} onClick={noop} onToggleStar={noop} />);
    expect(screen.getByText('projects.card.archived')).toBeTruthy();
    // Archived ring takes precedence over the stage segments: a full muted
    // circle with a check mark, not the segmented "index/total" ring.
    expect(screen.getByTestId('stage-ring-archived')).toBeTruthy();
    expect(screen.getByRole('img', { name: 'Archived' })).toBeTruthy();
    expect(screen.queryByText('6/6')).toBeNull();
  });

  // M2-W3-3 — workflow badge: current-node chip + amber agents-active chip,
  // fed by the batch-derived `workflow_badge` field (null → nothing rendered).
  describe('workflow badge (W3-3)', () => {
    it('renders the current-node chip and agents-active chip when active', () => {
      const project: Project = {
        ...base,
        workflow_badge: {
          current_node_name: 'Storyboard',
          workflow_total: 6,
          workflow_position: 2,
          agents_active: 3,
        },
      };
      render(<ProjectCard project={project} onClick={noop} onToggleStar={noop} />);
      expect(screen.getByTestId('project-workflow-stage-chip').textContent).toBe('Storyboard');
      const chip = screen.getByTestId('project-agents-active-chip');
      expect(chip).toBeTruthy();
      // Amber pulse dot present when agents are running.
      expect(chip.querySelector('.animate-pulse')).not.toBeNull();
    });

    it('renders the node chip but no agents chip when none are running', () => {
      const project: Project = {
        ...base,
        workflow_badge: {
          current_node_name: 'Editing',
          workflow_total: 6,
          workflow_position: 4,
          agents_active: 0,
        },
      };
      render(<ProjectCard project={project} onClick={noop} onToggleStar={noop} />);
      expect(screen.getByTestId('project-workflow-stage-chip')).toBeTruthy();
      expect(screen.queryByTestId('project-agents-active-chip')).toBeNull();
    });

    it('renders no workflow chip for a No-workflow project', () => {
      render(<ProjectCard project={base} onClick={noop} onToggleStar={noop} />);
      expect(screen.queryByTestId('project-workflow-stage-chip')).toBeNull();
      expect(screen.queryByTestId('project-agents-active-chip')).toBeNull();
    });
  });

  // PR-9 (G7) — grid secondary view: bottom next-action row driven by an
  // optional `suggestion` prop, absent by default (all prior tests above
  // pass no `suggestion` and must keep rendering exactly as before).
  describe('suggestion row (PR-9)', () => {
    const navigateSuggestion: ProjectSuggestionItem = {
      project_id: '1',
      name: base.name,
      stage_slug: 'review',
      kind: 'review_nav',
      stalled: false,
      action: { type: 'navigate', tab: 'shares', label_key: 'projects.suggest.cta_review', count: null },
    };

    const generateSuggestion: ProjectSuggestionItem = {
      project_id: '1',
      name: base.name,
      stage_slug: 'storyboard',
      kind: 'storyboard_generate',
      stalled: false,
      progress: { total: 12, done: 9, empty: 3, generating: 0, failed: 0, script_count: 2, scene_count: 5 },
      action: { type: 'generate_missing_frames', label_key: 'projects.suggest.ctaGenerate', count: 3 },
    };

    it('renders no suggestion row when the prop is absent', () => {
      render(<ProjectCard project={base} onClick={noop} onToggleStar={noop} />);
      expect(screen.queryByTestId('project-card-suggestion')).toBeNull();
    });

    it('renders the message + navigate CTA and calls onClick without touching generateMissingFrames', () => {
      const onClick = vi.fn();
      const gen = vi.spyOn(svc, 'generateMissingFrames');
      render(
        <ProjectCard project={base} onClick={onClick} onToggleStar={noop} suggestion={navigateSuggestion} />,
      );
      expect(screen.getByTestId('project-card-suggestion')).toBeTruthy();
      const cta = screen.getByTestId('project-card-suggestion-cta');
      fireEvent.click(cta);
      expect(onClick).toHaveBeenCalled();
      expect(gen).not.toHaveBeenCalled();
    });

    it('generate CTA fires generateMissingFrames and the refetch callback', async () => {
      const gen = vi
        .spyOn(svc, 'generateMissingFrames')
        .mockResolvedValue({ dispatched_count: 3, task_ids: ['t1', 't2', 't3'] });
      const onRefetch = vi.fn();
      render(
        <ProjectCard
          project={base}
          onClick={noop}
          onToggleStar={noop}
          suggestion={generateSuggestion}
          onSuggestionRefetch={onRefetch}
        />,
      );
      const cta = screen.getByTestId('project-card-suggestion-cta');
      fireEvent.click(cta);
      await waitFor(() => expect(gen).toHaveBeenCalledWith('1'));
      await waitFor(() => expect(onRefetch).toHaveBeenCalled());
    });
  });
});
