import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import { AudioWaveformPlayer } from './AudioWaveformPlayer';

beforeEach(() => {
  // jsdom lacks ResizeObserver (the waveform observes its container).
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  // Minimal AudioContext stub so the decode effect doesn't throw in jsdom.
  vi.stubGlobal(
    'AudioContext',
    class {
      decodeAudioData() {
        return Promise.resolve({ getChannelData: () => new Float32Array(0), length: 0 });
      }
      close() {
        return Promise.resolve();
      }
    },
  );
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ arrayBuffer: async () => new ArrayBuffer(0) }),
  );
});

describe('AudioWaveformPlayer chorus controls', () => {
  it('shows "Set chorus" and calls onChorusChange with current time when no chorus', () => {
    const onChorusChange = vi.fn();
    const { getByText } = render(
      <AudioWaveformPlayer src="blob:a" filename="a" chorusEditable onChorusChange={onChorusChange} />,
    );
    fireEvent.click(getByText('Set chorus'));
    expect(onChorusChange).toHaveBeenCalledWith(0); // currentTime starts at 0
  });

  it('shows "Clear" and calls onChorusChange(null) when chorus set', () => {
    const onChorusChange = vi.fn();
    const { getByText } = render(
      <AudioWaveformPlayer
        src="blob:a"
        filename="a"
        chorusStartSec={12}
        chorusEditable
        onChorusChange={onChorusChange}
      />,
    );
    fireEvent.click(getByText('Clear'));
    expect(onChorusChange).toHaveBeenCalledWith(null);
  });

  it('renders no chorus buttons when not editable', () => {
    const { queryByText } = render(
      <AudioWaveformPlayer src="blob:a" filename="a" chorusStartSec={12} />,
    );
    expect(queryByText('Set chorus')).toBeNull();
    expect(queryByText('Clear')).toBeNull();
  });
});
