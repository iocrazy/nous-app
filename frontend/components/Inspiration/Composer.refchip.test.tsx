import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const createNote = vi.fn();
vi.mock('../../services/inspirationService', () => ({
  createNote: (...a: unknown[]) => createNote(...a),
  uploadAttachment: vi.fn(),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: string) => f }) }));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('./NoteEditor', () => import('./testing/noteEditorShim'));

import { Composer } from './Composer';

const REF = { hotspot_id: '42', title: 'Silent vlog 2.1B', source: 'DOUYIN', heat: 98.4 };

describe('Composer ref-hotspot chip', () => {
  it('shows the reference card when prefill.refHotspot is set', () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} prefill={{ content: '', refHotspot: REF }} />);
    expect(screen.getByText('Silent vlog 2.1B')).toBeTruthy();
    expect(screen.getByText('DOUYIN')).toBeTruthy();
  });

  it('save passes the refHotspot to createNote', async () => {
    createNote.mockResolvedValue({ id: '1', attachments: [], tags: [] });
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} prefill={{ content: 'my take', refHotspot: REF }} />);
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(createNote).toHaveBeenCalledWith('my take', REF));
  });

  it('removing the chip drops the ref from the save', async () => {
    createNote.mockResolvedValue({ id: '1', attachments: [], tags: [] });
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} prefill={{ content: 'my take', refHotspot: REF }} />);
    fireEvent.click(screen.getByLabelText('Remove hotspot reference'));
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(createNote).toHaveBeenCalledWith('my take', undefined));
  });
});
