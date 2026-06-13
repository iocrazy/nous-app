/**
 * MemoryPanel — Claude-style memory management UI.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryPanel } from './MemoryPanel';
import type { MemoryProfile } from '../services/memoryService';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const mockService = vi.hoisted(() => ({
  getMemoryProfile: vi.fn(),
  setMemoryPrefs: vi.fn(),
  setMemoryCard: vi.fn(),
  deleteMemoryObservation: vi.fn(),
  forgetAllMemory: vi.fn(),
}));

vi.mock('../services/memoryService', () => mockService);

const profile: MemoryProfile = {
  workspace: 'default',
  service_available: true,
  learn_enabled: true,
  inject_enabled: false,
  card: ['Prefers bilingual output'],
  observations: [
    { id: 'c1', content: 'likes fast cuts under 2 seconds' },
    { id: 'c2', content: 'no lyrics in background music' },
  ],
};

describe('MemoryPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockService.getMemoryProfile.mockResolvedValue(profile);
    mockService.setMemoryPrefs.mockResolvedValue({ learn_enabled: true, inject_enabled: true });
    mockService.deleteMemoryObservation.mockResolvedValue(undefined);
    mockService.forgetAllMemory.mockResolvedValue({ deleted: 2 });
  });

  it('renders observations and card from the profile', async () => {
    render(<MemoryPanel />);
    expect(await screen.findByText('likes fast cuts under 2 seconds')).toBeTruthy();
    expect(screen.getByText('no lyrics in background music')).toBeTruthy();
    expect(
      (screen.getByPlaceholderText('memory.aboutPlaceholder') as HTMLTextAreaElement).value,
    ).toBe('Prefers bilingual output');
  });

  it('deletes an observation optimistically', async () => {
    render(<MemoryPanel />);
    await screen.findByText('likes fast cuts under 2 seconds');
    const delButtons = screen.getAllByTitle('memory.deleteObservation');
    fireEvent.click(delButtons[0]);
    await waitFor(() =>
      expect(mockService.deleteMemoryObservation).toHaveBeenCalledWith('c1'),
    );
    expect(screen.queryByText('likes fast cuts under 2 seconds')).toBeNull();
  });

  it('toggles a pref and persists it', async () => {
    render(<MemoryPanel />);
    await screen.findByText('likes fast cuts under 2 seconds');
    const switches = screen.getAllByRole('switch');
    fireEvent.click(switches[1]); // inject toggle (was false)
    await waitFor(() =>
      expect(mockService.setMemoryPrefs).toHaveBeenCalledWith({ inject_enabled: true }),
    );
  });

  it('requires confirmation before forgetting everything', async () => {
    render(<MemoryPanel />);
    await screen.findByText('likes fast cuts under 2 seconds');
    fireEvent.click(screen.getByText('memory.forgetAll'));
    expect(mockService.forgetAllMemory).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('memory.forgetYes'));
    await waitFor(() => expect(mockService.forgetAllMemory).toHaveBeenCalled());
  });
});
