/**
 * Phase 2a Task 7 — the three wire shapes a typed question arrives in, one
 * TypedQuestion out. Fixtures copy the REAL backend shapes (CLAUDE.md「边界
 * mock 必须用真实 JSON 形状」): the issue marker
 * (`execution_state.awaiting_input`, input_gate.build_awaiting_marker), the
 * run view (`metadata_json.view.question`, folds/question.py) and the chat
 * assistant metadata (`metadata_json.awaiting_input`, Task 4).
 */
import { describe, expect, it } from 'vitest';

import { questionFromChatMetadata, questionFromMarker, questionFromRunView } from './questionTypes';

const OPTIONS = [
  { label: 'Open ending', description: null },
  { label: 'Twist', description: 'A last-minute reversal' },
];

describe('questionFromMarker', () => {
  it('reads a typed marker', () => {
    const q = questionFromMarker({
      agent_outcome: 'needs_input',
      awaiting_input: {
        prompt: 'Which ending?',
        since: '2026-09-08T00:00:00Z',
        issue_id: 7,
        question_id: 'q:1:2',
        kind: 'user',
        options: OPTIONS,
        allow_free_text: false,
        run_id: '1',
      },
    });
    expect(q).toEqual({
      id: 'q:1:2',
      kind: 'user',
      prompt: 'Which ending?',
      options: [{ label: 'Open ending', description: undefined }, { label: 'Twist', description: 'A last-minute reversal' }],
      allowFreeText: false,
    });
  });

  it('is null for a legacy marker without a question id (plain needs_input)', () => {
    expect(questionFromMarker({ awaiting_input: { prompt: 'why?', since: 'T', issue_id: 7 } })).toBeNull();
    expect(questionFromMarker(null)).toBeNull();
    expect(questionFromMarker({})).toBeNull();
  });

  it('marks an already-answered marker (answered_at) as answered', () => {
    const q = questionFromMarker({
      awaiting_input: { prompt: 'p', question_id: 'q:1:2', kind: 'user', options: OPTIONS, allow_free_text: true, answered_at: 'T' },
    });
    expect(q?.answered).toEqual({ value: null });
  });
});

describe('questionFromRunView', () => {
  it('reads view.question (folded from question_asked)', () => {
    const q = questionFromRunView({
      v: 1,
      phase: 'waiting_input',
      question: { id: 'budget:42', kind: 'budget', prompt: 'Budget exhausted', options: OPTIONS, allow_free_text: false, asked_at: 'T' },
    });
    expect(q?.id).toBe('budget:42');
    expect(q?.kind).toBe('budget');
    expect(q?.allowFreeText).toBe(false);
    expect(q?.options.map((o) => o.label)).toEqual(['Open ending', 'Twist']);
  });

  it('is null when the view has no open question', () => {
    expect(questionFromRunView({ v: 1, phase: 'running', question: null })).toBeNull();
    expect(questionFromRunView(null)).toBeNull();
  });
});

describe('questionFromChatMetadata', () => {
  it('reads metadata_json.awaiting_input incl. the answered stamp', () => {
    const q = questionFromChatMetadata({
      awaiting_input: {
        question_id: 'q:77:4',
        kind: 'user',
        prompt: 'Which ending?',
        options: OPTIONS,
        allow_free_text: true,
        asked_at: 'T',
        run_id: '77',
        answered: { value: 'Twist', at: 'T2', superseded: false },
      },
    });
    expect(q?.id).toBe('q:77:4');
    expect(q?.answered).toEqual({ value: 'Twist', superseded: false });
  });

  it('reads a superseded stamp and ignores garbage', () => {
    const q = questionFromChatMetadata({
      awaiting_input: { question_id: 'q:1:1', kind: 'user', prompt: 'p', options: [], allow_free_text: true, answered: { value: null, superseded: true } },
    });
    expect(q?.answered).toEqual({ value: null, superseded: true });
    expect(questionFromChatMetadata({ awaiting_input: 'nope' })).toBeNull();
    expect(questionFromChatMetadata(null)).toBeNull();
    expect(questionFromChatMetadata({ awaiting_input: { kind: 'user' } })).toBeNull();
  });
});
