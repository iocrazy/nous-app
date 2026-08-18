/**
 * AIChatBubble (MessageBubble) — Task 6 wiring: the capability_denied notice.
 *
 * `useRunToolActivity` is mocked wholesale on purpose: its own fetch/cache/
 * dedup behaviour is pinned by toolActivity.test.ts and surfaces.test.tsx.
 * This file only pins that the panel reads `denials` off the hook and
 * renders CapabilityDeniedNotice — never the `activities` half, which would
 * double-render every tool_call (see the header comment in AIChatBubble.tsx
 * and toolActivity.ts's module docstring for why that split exists).
 */

import { fireEvent, render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { MessageBubble } from './AIChatBubble';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, arg2?: unknown) => {
      if (typeof arg2 === 'string') return arg2;
      if (arg2 && typeof arg2 === 'object') {
        return `${key}:${Object.values(arg2 as Record<string, unknown>).join(',')}`;
      }
      return key;
    },
  }),
}));

const useRunToolActivityMock = vi.fn();
vi.mock('../agentActivity/useRunToolActivity', () => ({
  useRunToolActivity: (...args: unknown[]) => useRunToolActivityMock(...args),
}));

beforeEach(() => {
  useRunToolActivityMock.mockReset();
  useRunToolActivityMock.mockReturnValue({ activities: [], denials: [], loaded: true });
});

describe('AIChatBubble — capability_denied notice wiring', () => {
  it('renders the notice with a CTA when the run has denials', () => {
    useRunToolActivityMock.mockReturnValue({
      activities: [],
      denials: [{ key: 'seq:1', tool: 'CreateShot', reason: 'blocked by permissions' }],
      loaded: true,
    });
    const { container } = render(
      <MemoryRouter>
        <MessageBubble role="assistant" content="done" runId="run-1" />
      </MemoryRouter>,
    );
    expect(
      container.querySelector('[data-testid="capability-denied-notice"]'),
    ).not.toBeNull();
    expect(container.querySelector('a[href="/settings?tab=ai"]')).not.toBeNull();
    // Reads (runId, false) — the panel must not poll a settled run's chips a
    // second time; only the live poll flag matters for a running turn.
    expect(useRunToolActivityMock).toHaveBeenCalledWith('run-1', false);
  });

  it('renders nothing extra when the hook reports no denials', () => {
    const { container } = render(
      <MemoryRouter>
        <MessageBubble role="assistant" content="done" runId="run-1" />
      </MemoryRouter>,
    );
    expect(
      container.querySelector('[data-testid="capability-denied-notice"]'),
    ).toBeNull();
  });
});

/**
 * Markdown rendering. Agents emit markdown; the bubble used to sanitize it as
 * HTML and inject it, so users saw literal `**bold**` / `> quote` syntax.
 * These pin the parse AND the two properties the removed sanitizer used to
 * provide (no live raw HTML, no javascript: hrefs) — react-markdown's
 * defaults are asserted here, not assumed.
 */
describe('AIChatBubble — markdown body', () => {
  const renderBody = (content: string): HTMLElement => {
    const { container } = render(
      <MemoryRouter>
        <MessageBubble role="assistant" content={content} />
      </MemoryRouter>,
    );
    const body = container.querySelector<HTMLElement>('[data-testid="bubble-markdown"]');
    expect(body).not.toBeNull();
    return body as HTMLElement;
  };

  it('parses emphasis instead of showing the syntax characters', () => {
    const body = renderBody('Hello **world** and _italics_');
    expect(body.querySelector('strong')?.textContent).toBe('world');
    expect(body.querySelector('em')?.textContent).toBe('italics');
    expect(body.textContent).not.toContain('**');
    expect(body.textContent).toContain('Hello world and italics');
  });

  it('parses headings, lists and blockquotes into real elements', () => {
    const body = renderBody('## Plan\n\n- first\n- second\n\n> quoted line');
    expect(body.querySelector('h2')?.textContent).toBe('Plan');
    const items = body.querySelectorAll('ul > li');
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toBe('first');
    expect(body.querySelector('blockquote')?.textContent).toContain('quoted line');
    expect(body.textContent).not.toContain('##');
    expect(body.textContent).not.toContain('> quoted');
  });

  it('does not emit a javascript: href for a markdown link', () => {
    const body = renderBody(
      '[click me](javascript:alert(1))\n\n[safe link](https://example.com/x)',
    );
    const anchors = body.querySelectorAll('a');
    // Both links must render, so the javascript: assertion below cannot pass
    // vacuously and we can see the transform is selective rather than
    // dropping every href.
    expect(anchors).toHaveLength(2);
    expect(anchors[1].getAttribute('href')).toBe('https://example.com/x');
    // react-markdown's defaultUrlTransform blanks the unsafe one. What must
    // never happen is a clickable javascript: URL reaching the DOM.
    expect(anchors[0].getAttribute('href') ?? '').not.toMatch(/^javascript:/i);
    expect(body.textContent).toContain('click me');
  });

  it('renders inline raw HTML as literal text, never as live elements', () => {
    const body = renderBody('before <img src=x onerror="alert(1)"> after');
    expect(body.querySelector('img')).toBeNull();
    expect(body.textContent).toContain('<img src=x onerror="alert(1)">');
  });

  it('copies the raw markdown source, not a tag-stripped rewrite', () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const source = 'Use **bold** and <not-a-tag> here';
    const { getByText } = render(
      <MemoryRouter>
        <MessageBubble role="assistant" content={source} />
      </MemoryRouter>,
    );
    fireEvent.click(getByText('chat.copy'));
    expect(writeText).toHaveBeenCalledWith(source);
  });
});
