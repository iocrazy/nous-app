/**
 * ProjectCollectModal — the collection deadline is the shared DateTimePopover
 * in `mode="single"`, not a native `<input type="date">`.
 *
 * Equivalence pins: `deadline` stays a plain 'YYYY-MM-DD' string, an unset
 * deadline still rides the create call as `undefined` (not `''`), and the
 * field is still clearable.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../services/projectsService', () => ({
  fetchProjectCollections: vi.fn().mockResolvedValue([]),
  createProjectCollection: vi.fn().mockResolvedValue({}),
  deleteProjectCollection: vi.fn().mockResolvedValue({}),
}));

vi.mock('./Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback?: string) => fallback ?? _k }),
}));

import { ProjectCollectModal } from './ProjectCollectModal';
import * as projectsService from '../services/projectsService';

async function openCreateForm() {
  render(<ProjectCollectModal projectId="p1" isOpen onClose={vi.fn()} />);
  fireEvent.click(await screen.findByText(/New Collection Link/));
  return screen.getByTestId('collect-deadline-trigger');
}

describe('ProjectCollectModal — deadline uses DateTimePopover', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(2026, 7, 13, 10, 0, 0)); // local 2026-08-13
  });

  afterEach(() => vi.useRealTimers());

  it('the trigger opens the popover and the picked day reaches the create call', async () => {
    const trigger = await openCreateForm();
    fireEvent.click(trigger);
    expect(screen.getByTestId('date-time-popover')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('2026-08-28'));
    expect(trigger.textContent).toContain('2026-08-28');

    fireEvent.change(screen.getByPlaceholderText('Collection name'), {
      target: { value: 'Season 2 Footage' },
    });
    fireEvent.click(screen.getByText('Create'));

    await waitFor(() =>
      expect(projectsService.createProjectCollection).toHaveBeenCalledWith('p1', {
        collection_name: 'Season 2 Footage',
        max_file_size_mb: 500,
        deadline: '2026-08-28',
      }),
    );
  });

  it('Clear empties the field, and an unset deadline stays `undefined` on the wire', async () => {
    const trigger = await openCreateForm();
    fireEvent.click(trigger);
    fireEvent.click(screen.getByLabelText('2026-08-28'));
    fireEvent.click(trigger);
    fireEvent.click(screen.getByTestId('date-time-clear'));

    expect(trigger.textContent).not.toContain('2026-08-28');

    fireEvent.change(screen.getByPlaceholderText('Collection name'), {
      target: { value: 'No deadline' },
    });
    fireEvent.click(screen.getByText('Create'));

    await waitFor(() =>
      expect(projectsService.createProjectCollection).toHaveBeenCalledWith('p1', {
        collection_name: 'No deadline',
        max_file_size_mb: 500,
        deadline: undefined,
      }),
    );
  });
});
