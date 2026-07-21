import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { TaskErrorNotice } from './TaskErrorNotice';

const ASR_CHAIN =
  'DBOSMaxStepRetriesExceeded: RuntimeError: Volcengine ASR query failed: ' +
  '45000006 [Invalid audio URI] OperatorWrapper Process failed: internal ' +
  'error,audio download failed';

describe('TaskErrorNotice', () => {
  it('shows the friendly message + hint, not the raw exception chain', () => {
    render(<TaskErrorNotice error={ASR_CHAIN} />);
    expect(
      screen.getByText("Transcription couldn't read this media's audio."),
    ).toBeTruthy();
    expect(screen.getByText(/background music download to finish/i)).toBeTruthy();
    // The friendly message is the headline — not tucked inside <details>.
    const headline = screen.getByText("Transcription couldn't read this media's audio.");
    expect(headline.closest('details')).toBeNull();
  });

  it('keeps the raw string available under a Details disclosure', () => {
    render(<TaskErrorNotice error={ASR_CHAIN} />);
    const details = screen.getByText('Details').closest('details');
    expect(details).toBeTruthy();
    // Raw exception text is preserved inside the <details>, nothing is lost.
    expect(details?.textContent).toContain('45000006');
    expect(details?.textContent).toContain('audio download failed');
  });

  it('renders nothing when there is no error', () => {
    const { container } = render(<TaskErrorNotice error={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('does not add a Details disclosure for already-clean short errors', () => {
    render(<TaskErrorNotice error="upload rejected" />);
    expect(screen.getByText('upload rejected')).toBeTruthy();
    expect(screen.queryByText('Details')).toBeNull();
  });
});
