/**
 * SpeakerChip — speaker-diarization label for a transcript segment.
 *   1. Renders the label with a color class when a speaker is present.
 *   2. Same speaker → same color; the mapping is deterministic (stable).
 *   3. Renders nothing when the segment has no speaker (old / non-diarized
 *      transcripts) — zero visual regression.
 */
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { SpeakerChip, speakerChipClass } from './SpeakerChip';

describe('SpeakerChip', () => {
  it('renders the speaker label when present', () => {
    const { getByTestId } = render(<SpeakerChip speaker="S01" />);
    const chip = getByTestId('speaker-chip');
    expect(chip.textContent).toBe('S01');
    expect(chip.getAttribute('data-speaker')).toBe('S01');
  });

  it('renders nothing when speaker is absent', () => {
    const { container } = render(<SpeakerChip speaker={undefined} />);
    expect(container.querySelector('[data-testid="speaker-chip"]')).toBeNull();
  });

  it('renders nothing for an empty speaker string', () => {
    const { container } = render(<SpeakerChip speaker="" />);
    expect(container.querySelector('[data-testid="speaker-chip"]')).toBeNull();
  });

  it('assigns a stable color per speaker (same label → same class)', () => {
    expect(speakerChipClass('S01')).toBe(speakerChipClass('S01'));
    expect(speakerChipClass('S02')).toBe(speakerChipClass('S02'));
  });

  it('gives different speakers distinct colors (first few labels)', () => {
    const classes = new Set(
      ['S01', 'S02', 'S03', 'S04'].map((s) => speakerChipClass(s)),
    );
    // The 8-color palette must not collapse the first four speakers to one hue.
    expect(classes.size).toBeGreaterThan(1);
  });

  it('applies the mapped color class to the rendered chip', () => {
    const { getByTestId } = render(<SpeakerChip speaker="S01" />);
    const expected = speakerChipClass('S01').split(' ')[0];
    expect(getByTestId('speaker-chip').className).toContain(expected);
  });
});
