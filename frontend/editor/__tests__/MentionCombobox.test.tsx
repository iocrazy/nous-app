import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

// i18n: echo the key so assertions are language-independent.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

import { MentionCombobox, filterMentionCandidates } from '../components/MentionCombobox';
import { scanMentionRuns } from '../render/layoutShared';

afterEach(() => {
  cleanup();
});

// ─── MentionCombobox (presentational listbox; ARIA lives on the focused line) ─

describe('MentionCombobox', () => {
  it('renders a listbox with one option per candidate, ids derived from listboxId', () => {
    render(
      <MentionCombobox
        candidates={['Ada', 'Blythe', 'Cy']}
        query=""
        listboxId="lb"
        activeIndex={0}
        onSelect={vi.fn()}
        onHover={vi.fn()}
      />,
    );
    // The popup is ONLY a listbox now — the combobox role lives on the line.
    expect(screen.queryByRole('combobox')).toBeNull();
    const listbox = screen.getByRole('listbox');
    expect(listbox).toHaveAttribute('id', 'lb');
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(3);
    expect(options[0]).toHaveAttribute('id', 'lb-opt-0');
    expect(options[0]).toHaveAttribute('aria-selected', 'true');
  });

  it('reflects the controlled activeIndex on aria-selected', () => {
    render(
      <MentionCombobox
        candidates={['Ada', 'Blythe', 'Cy']}
        query=""
        listboxId="lb"
        activeIndex={1}
        onSelect={vi.fn()}
        onHover={vi.fn()}
      />,
    );
    const options = screen.getAllByRole('option');
    expect(options[0]).toHaveAttribute('aria-selected', 'false');
    expect(options[1]).toHaveAttribute('aria-selected', 'true');
  });

  it('filters candidates by query, case-insensitively', () => {
    render(
      <MentionCombobox
        candidates={['Ada', 'Blythe', 'Cy']}
        query="y"
        listboxId="lb"
        activeIndex={0}
        onSelect={vi.fn()}
        onHover={vi.fn()}
      />,
    );
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(2); // Blythe, Cy
    expect(options.map((o) => o.textContent)).toEqual(['Blythe', 'Cy']);
    // Pure filter helper is exported and shared with SceneBlock.
    expect(filterMentionCandidates(['Ada', 'Blythe', 'Cy'], 'y')).toEqual(['Blythe', 'Cy']);
  });

  it('selects on option click without stealing focus (mousedown default prevented)', () => {
    const onSelect = vi.fn();
    render(
      <MentionCombobox
        candidates={['Ada', 'Blythe']}
        query=""
        listboxId="lb"
        activeIndex={0}
        onSelect={onSelect}
        onHover={vi.fn()}
      />,
    );
    fireEvent.mouseDown(screen.getByText('Blythe'));
    expect(onSelect).toHaveBeenCalledWith('Blythe');
  });

  it('shows a no-match hint and renders no options when nothing matches', () => {
    render(
      <MentionCombobox
        candidates={['Ada', 'Blythe']}
        query="zzz"
        listboxId="lb"
        activeIndex={0}
        onSelect={vi.fn()}
        onHover={vi.fn()}
      />,
    );
    expect(screen.queryAllByRole('option')).toHaveLength(0);
    expect(screen.getByText('editor.mentionNoMatch')).toBeInTheDocument();
  });
});

// ─── Mention run scanning (scanMentionRuns — single source of the chip ranges,
//     shared with the TipTap decoration plugin) ─────────────────────────────

describe('scanMentionRuns', () => {
  it('flags a known @name as known and an unknown one as a fallback run', () => {
    const runs = scanMentionRuns('Hi @Client and @Ghost', ['Client']);
    expect(runs.map((r) => ({ name: r.name, isKnown: r.isKnown }))).toEqual([
      { name: 'Client', isKnown: true },
      { name: 'Ghost', isKnown: false },
    ]);
  });

  it('returns no runs for plain text and empty input', () => {
    expect(scanMentionRuns('a < b', [])).toEqual([]);
    expect(scanMentionRuns('', [])).toEqual([]);
  });

  it('matches a multi-word KNOWN CAST name as one whole token', () => {
    const runs = scanMentionRuns('with @John Smith arriving', ['John Smith']);
    expect(runs).toHaveLength(1);
    expect(runs[0]).toMatchObject({ name: 'John Smith', isKnown: true });
    // The run ends before " arriving" (the trailing word is not swallowed).
    const text = 'with @John Smith arriving';
    expect(text.slice(runs[0].end)).toBe(' arriving');
  });

  it('matches only the first word of an UNKNOWN multi-word name', () => {
    const runs = scanMentionRuns('with @John Smith arriving', []);
    expect(runs).toHaveLength(1);
    expect(runs[0]).toMatchObject({ name: 'John', isKnown: false });
  });

  it('does not over-match a known name across a word boundary', () => {
    // "John Smith" must NOT match "@John Smithers" (the boundary char is a letter).
    const runs = scanMentionRuns('@John Smithers', ['John Smith']);
    expect(runs).toHaveLength(1);
    expect(runs[0].name).toBe('John');
  });

  it('skips a lone @ with nothing after it', () => {
    expect(scanMentionRuns('email me @ later', [])).toEqual([]);
  });
});
