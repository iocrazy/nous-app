import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useComposerPaste } from './useComposerPaste';

function _file(name: string, type: string): File {
  return new File(['data'], name, { type });
}

function _pasteEvent(files: File[]): React.ClipboardEvent {
  return {
    clipboardData: {
      files: files as unknown as FileList,
      // jsdom-ish stubs — hook only reads .files
      items: [] as unknown as DataTransferItemList,
      types: [],
      getData: () => '',
    } as unknown as DataTransfer,
    preventDefault: vi.fn(),
    stopPropagation: vi.fn(),
  } as unknown as React.ClipboardEvent;
}

describe('useComposerPaste', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('calls onFiles when clipboardData has files', () => {
    const onFiles = vi.fn();
    const { result } = renderHook(() => useComposerPaste({ onFiles }));
    const ev = _pasteEvent([_file('x.png', 'image/png')]);
    act(() => result.current.onPaste(ev));
    expect(ev.preventDefault).toHaveBeenCalled();
    expect(onFiles).toHaveBeenCalledTimes(1);
    expect(onFiles.mock.calls[0][0]).toBe(ev.clipboardData.files);
  });

  it('is a no-op for text-only paste (no files in clipboard)', () => {
    const onFiles = vi.fn();
    const { result } = renderHook(() => useComposerPaste({ onFiles }));
    const ev = _pasteEvent([]);
    act(() => result.current.onPaste(ev));
    // Must NOT preventDefault — otherwise the textarea wouldn't accept the pasted text.
    expect(ev.preventDefault).not.toHaveBeenCalled();
    expect(onFiles).not.toHaveBeenCalled();
  });

  it('disabled=true: skips onFiles even when files present', () => {
    const onFiles = vi.fn();
    const { result } = renderHook(() => useComposerPaste({ onFiles, disabled: true }));
    const ev = _pasteEvent([_file('x.png', 'image/png')]);
    act(() => result.current.onPaste(ev));
    // Hook still preventDefault on file paste so the file isn't dumped as text into the textarea.
    expect(ev.preventDefault).toHaveBeenCalled();
    expect(onFiles).not.toHaveBeenCalled();
  });

  it('handles missing clipboardData gracefully (no throw, no onFiles)', () => {
    const onFiles = vi.fn();
    const { result } = renderHook(() => useComposerPaste({ onFiles }));
    const ev = { preventDefault: vi.fn(), stopPropagation: vi.fn() } as unknown as React.ClipboardEvent;
    expect(() => act(() => result.current.onPaste(ev))).not.toThrow();
    expect(onFiles).not.toHaveBeenCalled();
    expect(ev.preventDefault).not.toHaveBeenCalled();
  });
});
