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

import { fireEvent, render, screen, within } from '@testing-library/react';
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

/**
 * User bubble — library reference chips.
 *
 * The attachment fixtures below are the REAL persisted shape, copied from
 * `public.messages` rather than from `AIChatMessageAttachment`: the reducer
 * (`conversations_ai_store.py::_DISPLAY_ATTACHMENT_KEYS`) keeps only
 * kind / resource_id / mime / alt_text / name and drops any key whose value
 * is None, so the rows are SPARSE — a fixture that always carries every
 * field would never exercise the branches that actually run in production.
 *
 * `resource_id` is a JSON **string** here, and that is not the usual
 * BIGINT-as-number case: `AttachmentRequest.resource_id` is typed `str`
 * with no numeric coercion, so a number never survives the request in the
 * first place, and the history read passes the column through untouched.
 */
const refAttachment = {
  kind: 'resource_ref',
  resource_id: '340888655925500',
  mime: 'video/mp4',
  alt_text: 'H3 node upgrade clip.mp4',
};

function renderUser(props: Partial<React.ComponentProps<typeof MessageBubble>>) {
  return render(
    <MemoryRouter>
      <MessageBubble role="user" content="" {...props} />
    </MemoryRouter>,
  );
}

describe('AIChatBubble — user bubble resource references', () => {
  it('paints a chip with the asset name and its cover', () => {
    renderUser({ content: 'Parse this video for me', attachments: [refAttachment] });

    const chip = screen.getByTestId('bubble-resource-chip');
    expect(within(chip).getByText('H3 node upgrade clip.mp4')).toBeInTheDocument();
    // The wire shape carries no thumbnail_url, so the cover can only be
    // addressed by id — and it must be absolute, or it resolves against the
    // Pages origin whose catch-all answers with index.html.
    const img = screen.getByTestId('bubble-resource-chip-thumb') as HTMLImageElement;
    expect(img.getAttribute('src')).toContain(
      '/api/v1/resources/340888655925500/cover',
    );
  });

  it('falls back to a kind icon when the cover 404s', () => {
    renderUser({ content: 'look', attachments: [refAttachment] });

    fireEvent.error(screen.getByTestId('bubble-resource-chip-thumb'));

    expect(screen.queryByTestId('bubble-resource-chip-thumb')).toBeNull();
    expect(screen.getByTestId('bubble-resource-chip-icon')).toBeInTheDocument();
  });

  it('renders an asset-only turn as chips instead of an empty bubble', () => {
    // A real row: the picker strips the `@query` trigger on send, so a turn
    // whose whole content was the mention persists with text = ''.
    const { container } = renderUser({ content: '', attachments: [refAttachment] });

    expect(screen.getByTestId('bubble-resource-chip')).toBeInTheDocument();
    expect(container.querySelector('p.whitespace-pre-wrap')).toBeNull();
  });

  it('wraps several references side by side', () => {
    renderUser({
      content: 'compare these',
      attachments: [
        refAttachment,
        { kind: 'resource_ref', resource_id: '340140594625790', mime: 'audio/mpeg', alt_text: 'take-2.mp3' },
      ],
    });

    expect(screen.getAllByTestId('bubble-resource-chip')).toHaveLength(2);
  });

  it('reads the issue-reply path\'s `name` key too', () => {
    // Same column, second live producer: issue_messages_router sends `name`
    // where this panel sends `alt_text`.
    renderUser({
      content: 'note',
      attachments: [{ kind: 'resource_ref', resource_id: '340888655925501', mime: 'application/pdf', name: 'brief.pdf' }],
    });

    expect(screen.getByText('brief.pdf')).toBeInTheDocument();
  });

  it('never requests a cover for the `str(None)` rows already in history', () => {
    renderUser({
      content: 'legacy',
      attachments: [{ kind: 'resource_ref', resource_id: 'None', mime: 'video/mp4', alt_text: 'orphan.mp4' }],
    });

    expect(screen.queryByTestId('bubble-resource-chip-thumb')).toBeNull();
    expect(screen.getByTestId('bubble-resource-chip-icon')).toBeInTheDocument();
  });

  it('labels a reference that lost both name keys', () => {
    renderUser({
      content: 'x',
      attachments: [{ kind: 'resource_ref', resource_id: '340888655925502', mime: 'video/mp4' }],
    });

    expect(screen.getByText('chat.attachments.resourceRef')).toBeInTheDocument();
  });

  it('leaves image attachments rendering as images', () => {
    const { container } = renderUser({
      content: 'see',
      attachments: [{ kind: 'image', resource_id: '340888655925503', mime: 'image/png', alt_text: 'shot.png' }],
    });

    expect(screen.queryByTestId('bubble-resource-chip')).toBeNull();
    expect(container.querySelector('img[alt="shot.png"]')).not.toBeNull();
  });

  // The wire's `kind` is always the literal 'resource_ref', so the asset's
  // own family can only come from mime. Without these the mapping could be
  // wired to any single icon and every other test would still pass.
  it.each([
    ['video/mp4', 'lucide-video'],
    ['audio/mpeg', 'lucide-music'],
    ['image/png', 'lucide-image'],
    ['application/pdf', 'lucide-file-type2'],
    [undefined, 'lucide-file-text'],
  ])('picks the icon family from mime %s', (mime, iconClass) => {
    // No resource_id, so the chip takes the icon branch and the icon is
    // the only thing under test.
    const { container } = renderUser({
      content: 'x',
      attachments: [{ kind: 'resource_ref', mime, alt_text: 'asset' }],
    });

    const icon = container.querySelector('[data-testid="bubble-resource-chip-icon"] svg');
    expect(icon?.getAttribute('class')).toContain(iconClass);
  });

  it('treats a whitespace-only turn as having no text', () => {
    // The composer can hand over a doc that serialises to spaces/newlines.
    // `content.length` would call that "has text" and paint the blank
    // paragraph again — the very bubble this fix removed.
    const { container } = renderUser({ content: '   \n  ', attachments: [refAttachment] });

    expect(screen.getByTestId('bubble-resource-chip')).toBeInTheDocument();
    expect(container.querySelector('p.whitespace-pre-wrap')).toBeNull();
  });

  it('renders no strip when the turn had no attachments', () => {
    renderUser({ content: 'plain text turn' });

    expect(screen.queryByTestId('bubble-resource-chip')).toBeNull();
    expect(screen.getByText('plain text turn')).toBeInTheDocument();
  });
});


describe('AIChatBubble — awaiting_input question card (phase 2a)', () => {
  const question = {
    id: 'q:77:4',
    kind: 'user',
    prompt: 'Which ending?',
    options: [{ label: 'Open ending' }, { label: 'Twist' }],
    allowFreeText: true,
  };

  it('mounts QuestionCard under the prose and answers through onAnswerQuestion', async () => {
    const onAnswerQuestion = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <MessageBubble
          role="assistant"
          content="Let me ask."
          awaitingInput={question}
          onAnswerQuestion={onAnswerQuestion}
        />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('bubble-question')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Twist' }));
    await vi.waitFor(() => expect(onAnswerQuestion).toHaveBeenCalledWith('Twist', 'q:77:4'));
  });

  it('renders an answered question read-only', () => {
    render(
      <MemoryRouter>
        <MessageBubble
          role="assistant"
          content="Let me ask."
          awaitingInput={{ ...question, answered: { value: 'Twist' } }}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole('button', { name: 'Twist' })).toBeDisabled();
  });

  it('draws nothing extra without awaitingInput', () => {
    render(
      <MemoryRouter>
        <MessageBubble role="assistant" content="plain" />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId('bubble-question')).toBeNull();
  });
});
