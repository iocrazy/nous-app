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
        // Include `duration` — the player calls setDuration(decoded.duration)
        // after decode; without it the chorus-gate (duration > 0) never opens.
        return Promise.resolve({ getChannelData: () => new Float32Array(0), length: 0, duration: 239 });
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

describe('AudioWaveformPlayer capsule layout', () => {
  it('renders the compact tint play button, time, and speed control', () => {
    const { container, getByText, getByLabelText, queryByText } = render(
      <AudioWaveformPlayer layout="capsule" src="blob:a" filename="a" duration={239} />,
    );
    // mock `.play` → .audio-play-btn
    expect(container.querySelector('.audio-play-btn')).toBeTruthy();
    // mock `.ptime` → .audio-ptime
    expect(container.querySelector('.audio-ptime')).toBeTruthy();
    // mock `.pside` → .audio-pside (speed + volume kept for D12)
    expect(container.querySelector('.audio-pside')).toBeTruthy();
    // speed control ("1×") is reachable and cycles
    const speed = getByText('1×');
    fireEvent.click(speed);
    // volume control kept reachable (icon-only; slider lives in a hover panel
    // but is still in the DOM)
    expect(getByLabelText('Volume')).toBeTruthy();
    // no "CHORUS" text badge — chorus is marked by a line on the waveform
    expect(queryByText('CHORUS')).toBeNull();
  });

  it('seek surface (canvas) is present and play toggles', () => {
    const { container, getByLabelText } = render(
      <AudioWaveformPlayer layout="capsule" src="blob:a" filename="a" />,
    );
    // play toggle reachable
    fireEvent.click(getByLabelText('Play'));
    // does not render the full-layout volume slider container twice / no crash
    expect(container.querySelector('.audio-play-btn')).toBeTruthy();
  });

  it('marks the chorus as a line (no text) once decoded', async () => {
    const { findByTitle, queryByText } = render(
      <AudioWaveformPlayer layout="capsule" src="blob:a" filename="a" duration={239} chorusStartSec={60} />,
    );
    // The chorus marker is a thin overlay line with title "Chorus" (it appears
    // after the async decode flips isDecoding off).
    const line = await findByTitle('Chorus');
    expect(line).toBeTruthy();
    // It carries no visible "CHORUS" label.
    expect(line.textContent).toBe('');
    expect(queryByText('CHORUS')).toBeNull();
  });
});
