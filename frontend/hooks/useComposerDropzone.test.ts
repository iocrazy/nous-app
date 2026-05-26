import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useComposerDropzone } from './useComposerDropzone';

function _file(name: string, type: string): File {
  return new File(['data'], name, { type });
}

function _dragEvent(type: string, files: File[] = []): React.DragEvent {
  // Minimal mock; only the fields the hook touches.
  const dt = {
    files: files as unknown as FileList,
    dropEffect: '',
    // Some browsers populate types/items; not used by the hook.
  };
  return {
    type,
    preventDefault: vi.fn(),
    stopPropagation: vi.fn(),
    dataTransfer: dt as unknown as DataTransfer,
  } as unknown as React.DragEvent;
}

describe('useComposerDropzone', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('isDragActive flips true on dragEnter, false on matching dragLeave', () => {
    const { result } = renderHook(() =>
      useComposerDropzone({ onFiles: vi.fn() }),
    );
    expect(result.current.isDragActive).toBe(false);
    act(() => result.current.rootProps.onDragEnter(_dragEvent('dragenter')));
    expect(result.current.isDragActive).toBe(true);
    act(() => result.current.rootProps.onDragLeave(_dragEvent('dragleave')));
    expect(result.current.isDragActive).toBe(false);
  });

  it('isDragActive stays true across nested dragEnter (child transitions)', () => {
    const { result } = renderHook(() =>
      useComposerDropzone({ onFiles: vi.fn() }),
    );
    // User enters wrapper (counter=1), then enters a child (counter=2).
    act(() => result.current.rootProps.onDragEnter(_dragEvent('dragenter')));
    act(() => result.current.rootProps.onDragEnter(_dragEvent('dragenter')));
    expect(result.current.isDragActive).toBe(true);
    // Leaves the child (counter=1) — should still be active.
    act(() => result.current.rootProps.onDragLeave(_dragEvent('dragleave')));
    expect(result.current.isDragActive).toBe(true);
    // Leaves the wrapper (counter=0) — now inactive.
    act(() => result.current.rootProps.onDragLeave(_dragEvent('dragleave')));
    expect(result.current.isDragActive).toBe(false);
  });

  it('onDragOver calls preventDefault and sets dropEffect=copy', () => {
    const { result } = renderHook(() =>
      useComposerDropzone({ onFiles: vi.fn() }),
    );
    const ev = _dragEvent('dragover');
    act(() => result.current.rootProps.onDragOver(ev));
    expect(ev.preventDefault).toHaveBeenCalled();
    expect(ev.dataTransfer.dropEffect).toBe('copy');
  });

  it('onDrop preventDefaults, calls onFiles with dropped files, resets isDragActive', async () => {
    const onFiles = vi.fn();
    const { result } = renderHook(() =>
      useComposerDropzone({ onFiles }),
    );
    act(() => result.current.rootProps.onDragEnter(_dragEvent('dragenter')));
    expect(result.current.isDragActive).toBe(true);

    const files = [_file('x.png', 'image/png')];
    const dropEv = _dragEvent('drop', files);
    await act(async () => {
      result.current.rootProps.onDrop(dropEv);
    });
    expect(dropEv.preventDefault).toHaveBeenCalled();
    expect(onFiles).toHaveBeenCalledTimes(1);
    expect(onFiles.mock.calls[0][0]).toBe(dropEv.dataTransfer.files);
    expect(result.current.isDragActive).toBe(false);
  });

  it('disabled=true: handlers still preventDefault (so browser does not navigate) but skip onFiles', async () => {
    const onFiles = vi.fn();
    const { result } = renderHook(() =>
      useComposerDropzone({ onFiles, disabled: true }),
    );
    const dropEv = _dragEvent('drop', [_file('x.png', 'image/png')]);
    await act(async () => {
      result.current.rootProps.onDrop(dropEv);
    });
    expect(dropEv.preventDefault).toHaveBeenCalled();
    expect(onFiles).not.toHaveBeenCalled();
    expect(result.current.isDragActive).toBe(false);
  });

  it('drop with empty files list is a no-op (no onFiles call)', async () => {
    const onFiles = vi.fn();
    const { result } = renderHook(() =>
      useComposerDropzone({ onFiles }),
    );
    const dropEv = _dragEvent('drop', []);
    await act(async () => {
      result.current.rootProps.onDrop(dropEv);
    });
    expect(onFiles).not.toHaveBeenCalled();
  });

  it('resets isDragActive when disabled flips to true mid-drag', () => {
    const { result, rerender } = renderHook(
      ({ disabled }: { disabled: boolean }) =>
        useComposerDropzone({ onFiles: vi.fn(), disabled }),
      { initialProps: { disabled: false } },
    );
    act(() => result.current.rootProps.onDragEnter(_dragEvent('dragenter')));
    expect(result.current.isDragActive).toBe(true);
    rerender({ disabled: true });
    expect(result.current.isDragActive).toBe(false);
  });
});
