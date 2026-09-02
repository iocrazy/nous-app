import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, it, expect, describe, beforeEach } from 'vitest';
import { NotesSidePanel } from './NotesSidePanel';
import type { InspirationNote } from '../../services/inspirationService';

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, defaultValue: string, options?: Record<string, unknown>) => {
      if (options?.count !== undefined) {
        return `${options.count} files`;
      }
      return defaultValue;
    },
  }),
}));

const NOTE = (id: string, md: string): InspirationNote => ({
  id,
  content_md: md,
  tags: [],
  ref_hotspot: null,
  pinned: false,
  rating: 0,
  note_date: '2026-07-10',
  created_at: '2026-07-10T09:00:00+00:00',
  updated_at: '2026-07-10T09:00:00+00:00',
  attachments: [],
  team_id: 'team-1',
  user_id: 'user-1',
  project_id: null,
});

describe('NotesSidePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders up to 3 recent notes (first line, truncated)', () => {
    render(
      <NotesSidePanel
        recentNotes={[NOTE('1', 'first idea\nsecond line'), NOTE('2', 'b'), NOTE('3', 'c')]}
        onQuickSave={vi.fn()}
        onOpenNotes={vi.fn()}
      />
    );
    expect(screen.getByText('first idea')).toBeTruthy();
    expect(screen.queryByText(/second line/)).toBeNull();
  });

  it('quick save submits trimmed content and clears', async () => {
    const onQuickSave = vi.fn().mockResolvedValue(undefined);
    render(<NotesSidePanel recentNotes={[]} onQuickSave={onQuickSave} onOpenNotes={vi.fn()} />);
    const input = screen.getByPlaceholderText('Quick note…') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '  quick #x  ' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(onQuickSave).toHaveBeenCalledWith('quick #x'));
    expect(input.value).toBe('');
  });

  it('empty input does not submit; open-notes fires', () => {
    const onQuickSave = vi.fn();
    const onOpenNotes = vi.fn();
    render(<NotesSidePanel recentNotes={[]} onQuickSave={onQuickSave} onOpenNotes={onOpenNotes} />);
    fireEvent.click(screen.getByText('Save'));
    expect(onQuickSave).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText(/open in notes/i));
    expect(onOpenNotes).toHaveBeenCalled();
  });
});
