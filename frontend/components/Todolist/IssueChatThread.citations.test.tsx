/**
 * 3a Task 8b-2 — a citation survives the round trip and shows up IN the thread.
 *
 * The composer has drawn `@<title> v<n>` chips since Task 6, but the thread
 * rendered the posted comment as bare text: a reader scrolling back saw a
 * sentence about "this shot" with nothing saying which object or which
 * revision it pointed at. The citation existed only in the seconds before the
 * send button.
 *
 * The fixtures are the REAL wire shape of `GET /issues/{id}/messages` — string
 * ids, `version` a number, `title` the registry snapshot — not a prettified
 * one (CLAUDE.md 边界 mock 必须用真实 JSON 形状).
 */
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { IssueChatThread } from './IssueChatThread';
import type { IssueMessage, IssueMessageAttachment } from '../../services/issueMessageService';

vi.mock('../../services/issueMessageService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/issueMessageService')>();
  return { ...actual, simulateAgentRunComplete: vi.fn() };
});
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

const CITATION: IssueMessageAttachment = {
  kind: 'output_ref',
  ref_kind: 'script_shot',
  ref_id: '9',
  version: 2,
  title: 'S1 · Shot 3',
};

function comment(over: Partial<IssueMessage> = {}): IssueMessage {
  return {
    id: 'm-1',
    issue_id: 1,
    kind: 'comment',
    author_user_id: 'u1',
    author_agent_id: null,
    body: 'tighten this one',
    meta: {},
    duration_seconds: null,
    agent_run_id: null,
    from_status: null,
    to_status: null,
    created_at: '2026-09-10T12:00:00Z',
    ...over,
  } as IssueMessage;
}

function draw(msg: IssueMessage) {
  return render(
    <MemoryRouter>
      <IssueChatThread messages={[msg]} agentsById={{}} selfUserId="u1" />
    </MemoryRouter>,
  );
}

describe('IssueChatThread — citation chips', () => {
  it('draws one chip per output_ref, naming the object AND the version', () => {
    const { container } = draw(comment({ attachments: [CITATION] }));
    const chips = container.querySelectorAll('[data-testid="staged-output-chip"]');
    expect(chips.length).toBe(1);
    const chip = chips[0] as HTMLElement;
    expect(chip.getAttribute('data-kind')).toBe('script_shot');
    expect(chip.getAttribute('data-ref')).toBe('9');
    // The version is the whole point: a chip naming only the object would mean
    // something different after the next revision.
    expect(chip.getAttribute('data-version')).toBe('2');
    expect(chip.textContent).toContain('@S1 · Shot 3');
    // The comment's own words are still there.
    expect(container.querySelector('[data-testid="comment-bubble"]')!.textContent).toContain(
      'tighten this one',
    );
  });

  it('the posted chip is read-only — no remove button in the thread', () => {
    // In the composer the X un-stages a draft citation. A sent comment is a
    // record: an X there would either lie (do nothing) or edit history.
    const { container } = draw(comment({ attachments: [CITATION] }));
    expect(container.querySelector('[data-testid="staged-output-chip-remove"]')).toBeNull();
  });

  it('draws a chip for each of several citations, in the posted order', () => {
    const second: IssueMessageAttachment = { ...CITATION, ref_id: '10', version: 1, title: null };
    const { container } = draw(comment({ attachments: [CITATION, second] }));
    const chips = [...container.querySelectorAll('[data-testid="staged-output-chip"]')];
    expect(chips.map((c) => c.getAttribute('data-ref'))).toEqual(['9', '10']);
  });

  it('a message with no attachments key renders exactly as it always did', () => {
    // Every row written before Task 6 — and every plain comment since.
    const { container } = draw(comment());
    expect(container.querySelector('[data-testid="staged-output-chip"]')).toBeNull();
    expect(container.querySelector('[data-testid="comment-bubble"]')).not.toBeNull();
  });

  it('ignores attachments that are not citations', () => {
    // An image or an asset_ref is a different member of the union with a
    // different render; only `output_ref` draws this chip.
    const image: IssueMessageAttachment = {
      kind: 'image',
      url: 'https://api.test/x.png',
      mime: 'image/png',
    };
    const { container } = draw(comment({ attachments: [image] }));
    expect(container.querySelector('[data-testid="staged-output-chip"]')).toBeNull();
  });
});
