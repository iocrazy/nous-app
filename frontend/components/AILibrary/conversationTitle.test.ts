/**
 * conversationTitle — what the workbench's recent-conversations row says.
 *
 * The row used to render `latest_output_summary` verbatim, so a run whose
 * output happened to be JSON dumped `{"shots": [{"title": ...` into the list
 * — unreadable, and long enough to blow the grid column out sideways. The
 * other branch was no better: falling back to `trigger` printed a bare
 * `script_ai`, which names the agent you are already looking at.
 *
 * Filtering that summary got the JSON off the screen but left every row
 * reading "Untitled conversation" (44 of 99 prod runs have no summary at all).
 * The server now projects a real `title` — conversation title → issue title →
 * first user message — and it takes precedence over everything here.
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

describe('conversationTitle — server-projected name', () => {
  it('prefers the server title over the run summary', () => {
    expect(
      conversationTitle(
        group({ title: 'Write the opening scene', latest_output_summary: 'Sure, here goes' }),
        FALLBACK,
      ),
    ).toBe('Write the opening scene');
  });

  it('uses the server title even when the summary is a JSON dump', () => {
    // The exact prod shape: a storyboard run named by its conversation.
    expect(
      conversationTitle(
        group({ title: 'Act two storyboard', latest_output_summary: '{"shots": []}' }),
        FALLBACK,
      ),
    ).toBe('Act two storyboard');
  });

  it('does not payload-filter the server title — a user may name a chat "{draft}"', () => {
    // The filter exists to keep AGENT OUTPUT off the row. A title is a name
    // someone chose; second-guessing it would silently rename their chat.
    expect(
      conversationTitle(group({ title: '{draft} pass one', latest_output_summary: null }), FALLBACK),
    ).toBe('{draft} pass one');
  });

  it('falls through to the summary when the server could not name the group', () => {
    // Pipeline runs (captioning, vision) have no conversation and no issue —
    // title is NULL by design, and half a readable sentence beats a label.
    expect(
      conversationTitle(
        group({ title: null, latest_output_summary: 'Second act outline' }),
        FALLBACK,
      ),
    ).toBe('Second act outline');
  });

  it('treats a blank or whitespace-only title as absent', () => {
    expect(
      conversationTitle(group({ title: '   ', latest_output_summary: 'Draft three' }), FALLBACK),
    ).toBe('Draft three');
    expect(
      conversationTitle(group({ title: undefined, latest_output_summary: 'Draft three' }), FALLBACK),
    ).toBe('Draft three');
  });

  it('still falls back when neither the title nor the summary is usable', () => {
    expect(
      conversationTitle(group({ title: null, latest_output_summary: '{"a":1}' }), FALLBACK),
    ).toBe(FALLBACK);
  });
});

describe('conversationTitle — machine output is never a title', () => {
  it('rejects a fenced JSON payload, not just a bare brace', () => {
    // Prod has plenty of ```json-wrapped output. Matching only `{` let those
    // through, so the row's title became the literal string "```json".
    const out = conversationTitle(
      group({ title: null, latest_output_summary: '```json\n{"prompt_en": "a girl"}\n```' }),
      FALLBACK,
    );
    expect(out).toBe(FALLBACK);
    expect(out).not.toContain('```');
  });

  it('rejects a bare fence with no language tag', () => {
    expect(
      conversationTitle(group({ title: null, latest_output_summary: '```\n{"a":1}' }), FALLBACK),
    ).toBe(FALLBACK);
  });

  it('rejects an XML/HTML-shaped payload', () => {
    expect(
      conversationTitle(
        group({ title: null, latest_output_summary: '<result><shot>wide</shot></result>' }),
        FALLBACK,
      ),
    ).toBe(FALLBACK);
  });
});

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
