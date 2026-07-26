// frontend/components/RedirectToTeam.test.tsx
// Task 4 fix (spec 2026-07-26-asset-prompt-management, C1): legacy flat
// routes like /canvas/:canvasId funnel personal-project users through
// RedirectToTeam into the team-scoped URL. <Navigate replace /> does NOT
// forward router `state` on its own — without an explicit `state` prop,
// payloads like SendToCanvasModal's `promptInsert` silently vanish at the
// redirect. This asserts the state prop is threaded through.

import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const navigateProps: Array<{ to: string; state: unknown; replace?: boolean }> = [];

vi.mock('react-router-dom', () => ({
  useLocation: () => ({ pathname: '/canvas/c1', state: { promptInsert: { assetId: 'r1' } } }),
  useParams: () => ({}),
  Navigate: (props: { to: string; state?: unknown; replace?: boolean }) => {
    navigateProps.push({ to: props.to, state: props.state, replace: props.replace });
    return null;
  },
}));

const useTeamContext = vi.fn();
vi.mock('../contexts/TeamContext', () => ({
  useTeamContext: () => useTeamContext(),
}));

import { RedirectToTeam } from './RedirectToTeam';

describe('RedirectToTeam', () => {
  it('forwards router state through the redirect', () => {
    navigateProps.length = 0;
    useTeamContext.mockReturnValue({
      selectedTeamId: null,
      personalTeamId: 'team-personal',
      teams: [{ id: 'team-personal' }],
      teamsLoading: false,
    });

    render(<RedirectToTeam view="canvas" />);

    expect(navigateProps).toHaveLength(1);
    expect(navigateProps[0].to).toBe('/team/team-personal/canvas/c1');
    expect(navigateProps[0].state).toEqual({ promptInsert: { assetId: 'r1' } });
    expect(navigateProps[0].replace).toBe(true);
  });
});
