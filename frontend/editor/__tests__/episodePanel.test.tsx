/**
 * EpisodePanel tests (Phase B P2, Task 5).
 *
 * The panel lists a project's episodes and drives their management: reassign the
 * current script to another episode (updateScriptProject), inline-rename an
 * episode (updateEpisode), New Episode (createEpisode), and delete — disabled
 * for a non-empty episode (script_count > 0, the RESTRICT FK).
 *
 * Episode ids are NATIVE NUMBERS at runtime though typed string (#1006), so the
 * fixture uses native ints to exercise the String()-coerced current comparison.
 */
import { render, screen, cleanup, fireEvent, within, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Episode } from '../sceneService';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const svc = vi.hoisted(() => ({
  createEpisode: vi.fn().mockResolvedValue({}),
  updateEpisode: vi.fn().mockResolvedValue({}),
  deleteEpisode: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('../sceneService', () => svc);

const scriptSvc = vi.hoisted(() => ({
  updateScriptProject: vi.fn().mockResolvedValue({}),
}));
vi.mock('../../services/scriptService', () => scriptSvc);

import { EpisodePanel } from '../components/EpisodePanel';

// Native-int ids (as they arrive at runtime) despite the string type.
const ep = (over: Partial<Episode> & { id: unknown }): Episode =>
  ({ title: 'Ep', sort_order: 0, script_count: 0, ...over }) as Episode;

const EPISODES: Episode[] = [
  ep({ id: 10 as unknown as string, title: 'Ep 1', sort_order: 0, script_count: 1 }),
  ep({ id: 11 as unknown as string, title: 'Ep 2', sort_order: 1, script_count: 0 }),
];

function renderPanel(currentEpisodeId: string | null = 10 as unknown as string) {
  return render(
    <EpisodePanel
      scriptId="900"
      projectId="700"
      episodes={EPISODES}
      currentEpisodeId={currentEpisodeId}
      onChanged={vi.fn()}
      onClose={vi.fn()}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('EpisodePanel', () => {
  it('renders every episode with its title', () => {
    renderPanel();
    expect(screen.getByText('Ep 1')).toBeInTheDocument();
    expect(screen.getByText('Ep 2')).toBeInTheDocument();
    expect(screen.getAllByTestId('episode-item')).toHaveLength(2);
  });

  it('reassigns the current script to another episode (updateScriptProject)', async () => {
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /Ep 2/ }));
    await waitFor(() =>
      expect(scriptSvc.updateScriptProject).toHaveBeenCalledWith('900', {
        episode_id: 11,
      }),
    );
  });

  it('does not reassign when the current episode row is clicked (disabled)', () => {
    renderPanel();
    // The current episode's main button is disabled — clicking is a no-op.
    const currentBtn = screen.getByRole('button', { name: /Ep 1/ });
    expect(currentBtn).toBeDisabled();
    fireEvent.click(currentBtn);
    expect(scriptSvc.updateScriptProject).not.toHaveBeenCalled();
  });

  it('renames an episode on double-click + Enter (updateEpisode)', async () => {
    renderPanel();
    fireEvent.dblClick(screen.getByRole('button', { name: /Ep 2/ }));
    const input = screen.getByLabelText('editor.epRename');
    fireEvent.change(input, { target: { value: 'Act Two' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() =>
      expect(svc.updateEpisode).toHaveBeenCalledWith(11, { title: 'Act Two' }),
    );
  });

  it('creates a new episode (createEpisode)', async () => {
    renderPanel();
    fireEvent.click(screen.getByText('editor.newEpisode'));
    await waitFor(() => expect(svc.createEpisode).toHaveBeenCalledWith('700'));
  });

  it('disables delete for a non-empty episode and deletes an empty one', async () => {
    renderPanel();
    const [itemA, itemB] = screen.getAllByTestId('episode-item');
    // Ep 1 has script_count 1 → delete disabled.
    expect(within(itemA).getByLabelText('editor.epDelete')).toBeDisabled();
    // Ep 2 has script_count 0 → deletable.
    const delB = within(itemB).getByLabelText('editor.epDelete');
    expect(delB).not.toBeDisabled();
    fireEvent.click(delB);
    await waitFor(() => expect(svc.deleteEpisode).toHaveBeenCalledWith(11));
  });
});
