import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { TempResourceActions } from './TempResourceActions';
import { tempTtlService } from '../services/tempTtlService';

vi.mock('../services/tempTtlService', () => ({
  tempTtlService: { promoteResource: vi.fn() },
}));

// FolderPickerModal real props: isOpen, onClose, onConfirm(folderId, libraryId?), mode, scopeType, scopeId
vi.mock('./FolderPickerModal', () => ({
  FolderPickerModal: ({
    isOpen,
    onConfirm,
    onClose,
  }: {
    isOpen: boolean;
    onConfirm: (folderId: string | null, libraryId?: string | null) => void;
    onClose: () => void;
  }) => {
    if (!isOpen) return null;
    return (
      <div data-testid="folder-picker">
        <button onClick={() => { onConfirm('f-target', null); onClose(); }}>pick</button>
      </div>
    );
  },
}));

describe('TempResourceActions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('Save button promotes to root (folderId=null)', async () => {
    (tempTtlService.promoteResource as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    const onDone = vi.fn();
    render(
      <TempResourceActions
        resourceId="res-1"
        scopeType="personal"
        scopeId="u1"
        onDone={onDone}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => {
      expect(tempTtlService.promoteResource).toHaveBeenCalledWith('res-1', {
        folderId: null,
        scopeType: 'personal',
        scopeId: 'u1',
      });
      expect(onDone).toHaveBeenCalled();
    });
  });

  it('arrow + picker promotes to the picked folder', async () => {
    (tempTtlService.promoteResource as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    const onDone = vi.fn();
    render(
      <TempResourceActions
        resourceId="res-1"
        scopeType="personal"
        scopeId="u1"
        onDone={onDone}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Save to folder/i }));
    fireEvent.click(await screen.findByText('pick'));
    await waitFor(() => {
      expect(tempTtlService.promoteResource).toHaveBeenCalledWith('res-1', {
        folderId: 'f-target',
        scopeType: 'personal',
        scopeId: 'u1',
      });
      expect(onDone).toHaveBeenCalled();
    });
  });

  it('disables both buttons while promote is in flight', async () => {
    let resolve!: (v: { success: boolean }) => void;
    (tempTtlService.promoteResource as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise((r) => { resolve = r; }),
    );
    render(
      <TempResourceActions
        resourceId="res-1"
        scopeType="personal"
        scopeId="u1"
        onDone={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    expect(screen.getByRole('button', { name: /^Save$/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Save to folder/i })).toBeDisabled();
    resolve({ success: true });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^Save$/ })).not.toBeDisabled();
    });
  });

  it('surfaces a promote error inline', async () => {
    (tempTtlService.promoteResource as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('500: boom'),
    );
    render(
      <TempResourceActions
        resourceId="res-1"
        scopeType="personal"
        scopeId="u1"
        onDone={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => {
      expect(screen.getByText(/boom/i)).toBeInTheDocument();
    });
  });
});
