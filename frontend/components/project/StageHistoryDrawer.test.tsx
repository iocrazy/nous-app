import { render, screen, waitFor } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { StageHistoryDrawer } from './StageHistoryDrawer';
import * as svc from '../../services/projectsService';

// relativeTime pulls in the real i18n instance via formatDate — stub it so
// this suite doesn't need initReactI18next (mirrors ProjectCard.test.tsx).
vi.mock('../../utils/relativeTime', () => ({
  formatRelativeTime: () => '2h ago',
}));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

test('renders history entries when open', async () => {
  vi.spyOn(svc, 'fetchStageHistory').mockResolvedValue([
    { id: '1', stage_slug: 'storyboard', stage_name: 'Storyboarding', entered_at: '2026-07-02T00:00:00', exited_at: null },
    { id: '2', stage_slug: 'script', stage_name: 'Scripting', entered_at: '2026-06-25T00:00:00', exited_at: '2026-07-02T00:00:00' },
  ]);
  render(<StageHistoryDrawer projectId="p1" open onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText('Storyboarding')).toBeInTheDocument());
  expect(screen.getByText('Scripting')).toBeInTheDocument();
});

test('renders nothing when closed', () => {
  const { container } = render(<StageHistoryDrawer projectId="p1" open={false} onClose={() => {}} />);
  expect(container.firstChild).toBeNull();
});
