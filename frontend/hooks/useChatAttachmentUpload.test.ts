import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useChatAttachmentUpload } from './useChatAttachmentUpload';
import { aiLibraryService } from '../services/aiLibraryService';
import * as pickerHelpers from '../components/ChatAttachmentPicker.helpers';

vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: { uploadChatAttachment: vi.fn() },
}));

// Hoisted addToast spy so tests can assert what reached the toast layer.
const addToastSpy = vi.fn();
vi.mock('../components/Toast', () => ({
  useToast: () => ({ addToast: addToastSpy }),
}));

function _file(name: string, type: string, content = 'data'): File {
  return new File([content], name, { type });
}

// jsdom does not implement DataTransfer, so we pass File[] directly.
// The hook accepts File[] | FileList | null, so this is a valid call path.
function _fileList(files: File[]): File[] {
  return files;
}

describe('useChatAttachmentUpload', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    addToastSpy.mockClear();
  });

  it('uploads each file via aiLibraryService and appends to attachments', async () => {
    (aiLibraryService.uploadChatAttachment as ReturnType<typeof vi.fn>).mockResolvedValue({
      kind: 'image',
      url: 'personal/u1/temp/x.png',
      filename: 'x.png',
      size_bytes: 100,
      mime: 'image/png',
      resource_id: 'res-1',
      file_path: 'personal/u1/temp/x.png',
    });

    const onChange = vi.fn();
    const { result } = renderHook(() =>
      useChatAttachmentUpload({ attachments: [], onChange }),
    );

    await act(async () => {
      await result.current.handleFiles(_fileList([_file('x.png', 'image/png')]));
    });

    expect(aiLibraryService.uploadChatAttachment).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalled();
    const next = onChange.mock.calls[0][0];
    expect(next).toHaveLength(1);
    expect(next[0].kind).toBe('image');
    expect(next[0].url).toBe('personal/u1/temp/x.png');
  });

  it('flips uploading true while in flight, false when done', async () => {
    let resolveUpload!: (v: unknown) => void;
    (aiLibraryService.uploadChatAttachment as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise((r) => { resolveUpload = r; }),
    );

    const { result } = renderHook(() =>
      useChatAttachmentUpload({ attachments: [], onChange: vi.fn() }),
    );

    let donePromise!: Promise<void>;
    act(() => {
      donePromise = result.current.handleFiles(_fileList([_file('x.png', 'image/png')]));
    });
    expect(result.current.uploading).toBe(true);

    await act(async () => {
      resolveUpload({
        kind: 'image', url: 'personal/u1/temp/x.png', filename: 'x.png',
        size_bytes: 1, mime: 'image/png',
      });
      await donePromise;
    });
    await waitFor(() => expect(result.current.uploading).toBe(false));
  });

  it('handles null fileList as a no-op', async () => {
    const onChange = vi.fn();
    const { result } = renderHook(() =>
      useChatAttachmentUpload({ attachments: [], onChange }),
    );
    await act(async () => { await result.current.handleFiles(null); });
    expect(onChange).not.toHaveBeenCalled();
    expect(aiLibraryService.uploadChatAttachment).not.toHaveBeenCalled();
  });

  it('preserves existing attachments when appending', async () => {
    (aiLibraryService.uploadChatAttachment as ReturnType<typeof vi.fn>).mockResolvedValue({
      kind: 'image', url: 'new.png', filename: 'new.png',
      size_bytes: 1, mime: 'image/png',
    });

    const existing = [
      { kind: 'image' as const, url: 'old.png', filename: 'old.png', size_bytes: 1, mime: 'image/png' },
    ];
    const onChange = vi.fn();
    const { result } = renderHook(() =>
      useChatAttachmentUpload({ attachments: existing, onChange }),
    );

    await act(async () => {
      await result.current.handleFiles(_fileList([_file('new.png', 'image/png')]));
    });

    const next = onChange.mock.calls[0][0];
    expect(next).toHaveLength(2);
    expect(next[0].url).toBe('old.png');
    expect(next[1].url).toBe('new.png');
  });

  it('routes validateFileBatch errors through translateError before toast', async () => {
    // Force validateFileBatch to return a synthetic i18n key so we can
    // assert routing without relying on real validation rules.
    const validateSpy = vi
      .spyOn(pickerHelpers, 'validateFileBatch')
      .mockReturnValue('chat.attachments.fakeError');

    const translateError = vi.fn((k: string) => `TRANSLATED:${k}`);
    const onChange = vi.fn();

    const { result } = renderHook(() =>
      useChatAttachmentUpload({ attachments: [], onChange, translateError }),
    );

    await act(async () => {
      await result.current.handleFiles(_fileList([_file('hax.exe', 'application/octet-stream')]));
    });

    expect(validateSpy).toHaveBeenCalled();
    expect(translateError).toHaveBeenCalledWith('chat.attachments.fakeError');
    expect(addToastSpy).toHaveBeenCalledWith('TRANSLATED:chat.attachments.fakeError', 'error');
    // Validation failure short-circuits — no upload, no onChange.
    expect(aiLibraryService.uploadChatAttachment).not.toHaveBeenCalled();
    expect(onChange).not.toHaveBeenCalled();

    validateSpy.mockRestore();
  });
});
