/**
 * The four kind tables must cover the four kinds — at RUNTIME as well as at
 * compile time (harness 3a ticket A5).
 *
 * `Record<DeliverableKind, …>` already makes a missing key a compile error, so
 * why assert it again here? Because that guarantee is one keystroke deep: the
 * tables were `Record<string, …>` until this ticket, and widening one back is
 * an edit nobody reviews twice — it silences tsc without changing a single
 * value, which is the shape of drift that ships. This file fails on the
 * widening itself, so the protection cannot be removed quietly.
 *
 * The list this checks against is `DELIVERABLE_KINDS`, whose agreement with
 * Python is pinned on the other side by
 * `backend/tests/services/deliverables/test_kinds_frontend_mirror.py`. Neither
 * test can stand alone: this one would happily agree with a TS list that has
 * drifted from the registry, and that one cannot see whether the tables use it.
 */
import { describe, it, expect } from 'vitest';

import { DELIVERABLE_KINDS } from './deliverableKinds';
import { KIND_WORDS } from './outputMentionRows';
import { KIND_LABEL as MENTION_LIST_LABELS } from './OutputMentionList';
import { KIND_LABEL as CHIP_LABELS } from './OutputChipBody';
import { KIND_LABEL as OUTPUTS_BLOCK_LABELS } from '../Todolist/blocks/OutputsBlock';

const TABLES: Record<string, Record<string, unknown>> = {
  'outputMentionRows KIND_WORDS': KIND_WORDS,
  'OutputMentionList KIND_LABEL': MENTION_LIST_LABELS,
  'OutputChipBody KIND_LABEL': CHIP_LABELS,
  'OutputsBlock KIND_LABEL': OUTPUTS_BLOCK_LABELS,
};

describe('the deliverable-kind tables', () => {
  it.each(Object.keys(TABLES))('%s covers exactly the four kinds', (name) => {
    // Sorted sets, not `toEqual` on the arrays: declaration order inside a
    // label table is a rendering detail (nothing iterates these), unlike
    // `DELIVERABLE_KINDS` itself, whose order IS pinned against Python.
    expect(Object.keys(TABLES[name]).sort()).toEqual([...DELIVERABLE_KINDS].sort());
  });

  it('would notice a table that had grown a key of its own', () => {
    // A control on the assertion above: if it compared only "every kind is
    // present" it would pass on a table carrying a fifth, dead entry — a label
    // for something the backend never registers, which reads as support.
    const widened = { ...CHIP_LABELS, script_beat: ['outputs.kindBeat', 'Beat'] };
    expect(Object.keys(widened).sort()).not.toEqual([...DELIVERABLE_KINDS].sort());
  });

  it('would notice a table that had lost a kind', () => {
    const { script_chapter: _dropped, ...narrowed } = CHIP_LABELS;
    expect(Object.keys(narrowed).sort()).not.toEqual([...DELIVERABLE_KINDS].sort());
  });

  it('lists the four kinds the registry knows, in the registry order', () => {
    // A literal, deliberately. The backend mirror test is what makes this
    // honest; repeating the words here means a hand edit to the constant is
    // caught on the side that does not need a Python interpreter, and the two
    // failures together name both halves of the drift.
    expect([...DELIVERABLE_KINDS]).toEqual([
      'generated_media',
      'script_shot',
      'script_scene',
      'script_chapter',
    ]);
  });
});
