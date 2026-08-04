/**
 * conversationTitle — what the workbench's recent-conversations row says.
 *
 * The row used to render `latest_output_summary` verbatim, so a run whose
 * output happened to be JSON dumped `{"shots": [{"title": ...` into the list
 * — unreadable, and long enough to blow the grid column out sideways. The
 * other branch was no better: falling back to `trigger` printed a bare
 * `script_ai`, which names the agent you are already looking at.
 */
import { describe, it, expect } from 'vitest';
import { conversationTitle } from './conversationTitle';

const FALLBACK = 'Untitled conversation';

const group = (over: Record<string, unknown> = {}) =>
  ({
    latest_output_summary: 'Second act outline',
    trigger: 'chat',
    ...over,
  }) as never;

describe('conversationTitle', () => {
  it('uses the run summary when it reads like prose', () => {
    expect(conversationTitle(group(), FALLBACK)).toBe('Second act outline');
  });

  it('trims surrounding whitespace', () => {
    expect(conversationTitle(group({ latest_output_summary: '  Draft three  ' }), FALLBACK)).toBe(
      'Draft three',
    );
  });

  it('falls back when the summary is a JSON object dump', () => {
    expect(
      conversationTitle(group({ latest_output_summary: '{"shots": [{"title": "a"}]}' }), FALLBACK),
    ).toBe(FALLBACK);
  });

  it('falls back when the summary is a JSON array dump', () => {
    expect(
      conversationTitle(group({ latest_output_summary: '[ { "title": "scene 1" } ]' }), FALLBACK),
    ).toBe(FALLBACK);
  });

  it('detects a JSON dump even behind leading whitespace or a newline', () => {
    expect(conversationTitle(group({ latest_output_summary: '\n  [\n  {"a":1}' }), FALLBACK)).toBe(
      FALLBACK,
    );
  });

  it('falls back on an empty or whitespace-only summary', () => {
    expect(conversationTitle(group({ latest_output_summary: '   ' }), FALLBACK)).toBe(FALLBACK);
    expect(conversationTitle(group({ latest_output_summary: null }), FALLBACK)).toBe(FALLBACK);
    expect(conversationTitle(group({ latest_output_summary: undefined }), FALLBACK)).toBe(FALLBACK);
  });

  it('never falls back to the trigger — that names the agent, not the chat', () => {
    // The old code did `summary || trigger`, which surfaced a bare `script_ai`.
    expect(
      conversationTitle(group({ latest_output_summary: null, trigger: 'script_ai' }), FALLBACK),
    ).toBe(FALLBACK);
  });

  it('keeps only the first line — a summary may be a whole paragraph', () => {
    expect(
      conversationTitle(group({ latest_output_summary: 'Act two\n\nthen a lot more prose' }), FALLBACK),
    ).toBe('Act two');
  });

  it('collapses runs of whitespace so the row cannot fake its own width', () => {
    expect(
      conversationTitle(group({ latest_output_summary: 'Act    two   revised' }), FALLBACK),
    ).toBe('Act two revised');
  });

  it('caps a very long single-line summary', () => {
    const long = 'x'.repeat(500);
    const out = conversationTitle(group({ latest_output_summary: long }), FALLBACK);
    expect(out.length).toBeLessThanOrEqual(120);
    expect(out.endsWith('…')).toBe(true);
  });

  it('leaves prose that merely mentions a brace alone', () => {
    expect(
      conversationTitle(group({ latest_output_summary: 'Use {name} as the placeholder' }), FALLBACK),
    ).toBe('Use {name} as the placeholder');
  });
});
