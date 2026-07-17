import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { IssueCommentTriggerChip } from './IssueCommentTriggerChip';

const PREVIEW = { will_wake: true, agent_id: 'agent-1' };

function setup(over: Partial<React.ComponentProps<typeof IssueCommentTriggerChip>> = {}) {
  const onToggle = vi.fn();
  const utils = render(
    <IssueCommentTriggerChip
      preview={PREVIEW}
      agentName="Scriptwriter"
      suppressed={false}
      draftEmpty={false}
      onToggle={onToggle}
      {...over}
    />,
  );
  return { onToggle, ...utils };
}

describe('IssueCommentTriggerChip', () => {
  it('names the agent a comment will wake', () => {
    setup();
    expect(screen.getByTestId('comment-trigger-chip')).toHaveTextContent(
      /Will start when sent/i,
    );
    expect(screen.getByTestId('comment-trigger-chip')).toHaveTextContent('Scriptwriter');
  });

  it('stays hidden when no agent would wake', () => {
    setup({ preview: { will_wake: false, agent_id: null } });
    expect(screen.queryByTestId('comment-trigger-chip')).toBeNull();
  });

  it('stays hidden while the draft is empty', () => {
    // Nothing is about to be sent, so there is nothing to warn about — this
    // mirrors multica's shouldRenderComposerHandoffPreview.
    setup({ draftEmpty: true });
    expect(screen.queryByTestId('comment-trigger-chip')).toBeNull();
  });

  it('stays hidden when a wake has no agent to opt out of', () => {
    // will_wake:true + agent_id:null can't be suppressed (nothing to name), so
    // rendering it would be an un-dismissible dead end. Disclose nothing rather
    // than a stuck toggle.
    setup({ preview: { will_wake: true, agent_id: null } });
    expect(screen.queryByTestId('comment-trigger-chip')).toBeNull();
  });

  it('reads as a pressed toggle once suppressed', () => {
    setup({ suppressed: true });
    const chip = screen.getByTestId('comment-trigger-chip');
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    expect(chip).toHaveTextContent(/Won't start this time/i);
    expect(chip).toHaveTextContent(/Click to restore/i);
  });

  it('reads as an unpressed toggle by default', () => {
    setup();
    expect(screen.getByTestId('comment-trigger-chip')).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('says "this time" — suppressing skips the run, it does not hide the note', () => {
    // The agent still reads a suppressed note on its next wake, so the copy must
    // not imply the message is invisible to it.
    setup({ suppressed: true });
    const chip = screen.getByTestId('comment-trigger-chip');
    expect(chip.textContent).toMatch(/this time/i);
    expect(chip.textContent).not.toMatch(/never|won't see|hidden/i);
  });

  it('hands the toggle back to the parent', () => {
    const { onToggle } = setup();
    fireEvent.click(screen.getByTestId('comment-trigger-chip'));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('falls back to a generic label when the agent name has not resolved', () => {
    // The endpoint is a pure predicate — it returns an id, and the caller
    // resolves the name. A rename mid-flight must not render "undefined".
    setup({ agentName: undefined });
    const chip = screen.getByTestId('comment-trigger-chip');
    expect(chip).toHaveTextContent(/Will start when sent/i);
    expect(chip.textContent).not.toMatch(/undefined|null/i);
  });

  // ── Note state (/note keyboard flow) ─────────────────────────────────────
  describe('note state', () => {
    const NOTE = { will_wake: false, agent_id: 'agent-1', is_note: true };

    it('renders the quiet-note disclosure naming who is not woken', () => {
      setup({ preview: NOTE });
      const chip = screen.getByTestId('comment-trigger-chip');
      expect(chip).toHaveTextContent(/Quiet note/i);
      expect(chip).toHaveTextContent(/won't wake/i);
      expect(chip).toHaveTextContent('Scriptwriter');
    });

    it('is not a toggle — clicking a note chip does nothing', () => {
      // /note is a keyboard intent: undone by editing the draft, not by
      // clicking. The note state renders as a static span, not a button.
      const { onToggle } = setup({ preview: NOTE });
      const chip = screen.getByTestId('comment-trigger-chip');
      expect(chip.tagName).not.toBe('BUTTON');
      fireEvent.click(chip);
      expect(onToggle).not.toHaveBeenCalled();
    });

    it('stays hidden while the draft is empty even for a note verdict', () => {
      // Defensive: a note verdict with an empty draft shouldn't happen (the
      // /note text IS the draft), but nothing-to-send means nothing to disclose.
      setup({ preview: NOTE, draftEmpty: true });
      expect(screen.queryByTestId('comment-trigger-chip')).toBeNull();
    });

    it('stays hidden for a note on an unassigned issue', () => {
      // No agent → nothing billable was ever at stake; the chip's job is
      // billing disclosure, so there is nothing to say.
      setup({ preview: { will_wake: false, agent_id: null, is_note: true } });
      expect(screen.queryByTestId('comment-trigger-chip')).toBeNull();
    });

    it('note state wins over a pending suppression', () => {
      // The user suppressed, then typed /note: the note copy is the truthful
      // one (the /note prefix alone already guarantees no wake).
      setup({ preview: NOTE, suppressed: true });
      const chip = screen.getByTestId('comment-trigger-chip');
      expect(chip).toHaveTextContent(/Quiet note/i);
      expect(chip.textContent).not.toMatch(/Click to restore/i);
    });

    it('falls back to a generic label when the agent name has not resolved', () => {
      setup({ preview: NOTE, agentName: undefined });
      const chip = screen.getByTestId('comment-trigger-chip');
      expect(chip).toHaveTextContent(/won't wake the assigned agent/i);
      expect(chip.textContent).not.toMatch(/undefined|null/i);
    });
  });
});
