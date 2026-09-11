/**
 * Phase 2a Task 9 walkthrough finding: the cockpit's inbox line rendered
 * "2 message waiting for the agent". Plural copy uses i18next's
 * `_one` / `_other` pair in BOTH locales (repo convention, e.g.
 * appearsInCanvases_one/_other) — a bare key cannot pluralise.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const load = (lang: string) =>
  JSON.parse(readFileSync(resolve(__dirname, `../../../public/locales/${lang}.json`), 'utf8')) as Record<string, Record<string, string>>;

describe('issueDetail.inboxPending is a plural pair', () => {
  it.each(['en', 'zh'])('%s carries _one and _other and no bare key', (lang) => {
    const d = load(lang).issueDetail;
    expect(d.inboxPending_one).toContain('{{count}}');
    expect(d.inboxPending_other).toContain('{{count}}');
    expect(d.inboxPending).toBeUndefined();
  });

  it('en reads as English plural', () => {
    const d = load('en').issueDetail;
    expect(d.inboxPending_one).toBe('{{count}} message waiting for the agent');
    expect(d.inboxPending_other).toBe('{{count}} messages waiting for the agent');
  });
});


describe('issueDetail.toolsTimedOut is a plural pair (harness 2b-1 §3)', () => {
  it.each(['en', 'zh'])('%s carries _one and _other, no bare key', (lang) => {
    const d = load(lang).issueDetail;
    expect(d.toolsTimedOut_one).toContain('{{count}}');
    expect(d.toolsTimedOut_other).toContain('{{count}}');
    expect(d.toolsTimedOut).toBeUndefined();
    expect(typeof d.tools).toBe('string');
  });
  it('zh is not the English copy', () => {
    expect(load('zh').issueDetail.toolsTimedOut_one).not.toBe(load('en').issueDetail.toolsTimedOut_one);
    expect(load('zh').trajectory.timedOut).not.toBe(load('en').trajectory.timedOut);
  });
});


describe('the outputs copy exists in both locales (harness 3a §5)', () => {
  // Every key the cell, the block, the cards and the dialog ask for. A key
  // that exists in `en` alone renders English inside a Chinese UI; a key
  // missing from both renders the raw dotted path.
  const OUTPUTS_KEYS = [
    'version', 'replaced', 'open', 'diff', 'revert', 'revertHint', 'changeCount', 'truncated',
    'openRun', 'openRunHint',
    'noSnapshot', 'noLedger', 'notFound', 'unavailable', 'noPreview', 'loadFailed',
    'errorNotRegistered', 'errorVersionNotFound', 'errorCode', 'errorGeneric',
    'kindMedia', 'kindShot', 'kindScene', 'kindChapter', 'kindOther',
  ];

  it.each(['en', 'zh'])('%s has the outputs namespace and the cockpit cell copy', (lang) => {
    const all = load(lang);
    for (const key of OUTPUTS_KEYS) expect(typeof all.outputs?.[key]).toBe('string');
    expect(typeof all.issueDetail.outputs).toBe('string');
    expect(all.issueDetail.outputsRevised).toContain('{{n}}');
  });

  it.each(['en', 'zh'])('%s pluralises the revision count as a _one/_other pair', (lang) => {
    const o = load(lang).outputs;
    expect(o.revisions_one).toContain('{{count}}');
    expect(o.revisions_other).toContain('{{count}}');
    expect(o.revisions).toBeUndefined();
  });

  it('zh is a translation, not a copy of the English', () => {
    const en = load('en');
    const zh = load('zh');
    expect(zh.outputs.loadFailed).not.toBe(en.outputs.loadFailed);
    expect(zh.outputs.errorNotRegistered).not.toBe(en.outputs.errorNotRegistered);
    expect(zh.issueDetail.outputs).not.toBe(en.issueDetail.outputs);
  });
});

describe('the citation + provenance copy exists in both locales (harness 3a Task 6)', () => {
  // Task 6 adds three user-visible surfaces: the @-picker's Outputs tab, the
  // staged-citation chip, and the object-page provenance block. A key present
  // in `en` alone renders English inside a Chinese UI; one missing from both
  // renders the raw dotted path at a user.
  const TASK6_KEYS = [
    'mentionTab', 'mentionLoading', 'mentionEmpty', 'mentionError', 'mentionOlder',
    'citationRemove', 'citationLimit',
    'provenance', 'provenanceOpenIssue', 'provenanceOpenRun', 'provenanceDiff',
    'provenanceStep', 'provenanceNoLink', 'provenanceFailed',
  ];

  it.each(['en', 'zh'])('%s has every Task 6 key', (lang) => {
    const o = load(lang).outputs;
    for (const key of TASK6_KEYS) expect(typeof o?.[key]).toBe('string');
  });

  it.each(['en', 'zh'])('%s refuses a citation in words, with the cap interpolated', (lang) => {
    const r = load(lang).outputs.refError as unknown as Record<string, string>;
    expect(typeof r.unresolvable).toBe('string');
    expect(r.limitExceeded).toContain('{{n}}');
    // The digit would go stale the day the cap moves — the backend mirror test
    // pins this from the other side too.
    expect(r.limitExceeded).not.toContain('8');
    expect(r.unknown).toContain('{{code}}');
  });

  it.each(['en', 'zh'])('%s pluralises the two counted phrases as _one/_other pairs', (lang) => {
    const o = load(lang).outputs;
    for (const base of ['mentionCount', 'provenanceVersions']) {
      expect(o[`${base}_one`]).toContain('{{count}}');
      expect(o[`${base}_other`]).toContain('{{count}}');
      expect(o[base]).toBeUndefined();
    }
  });

  it('the generated card has its own issue hint, distinct from the canvas one', () => {
    // One hint for two destinations would tell a reader the link opens a
    // canvas when it opens an issue.
    for (const lang of ['en', 'zh']) {
      const card = load(lang).generated as unknown as Record<string, Record<string, string>>;
      expect(typeof card.card.openIssueHint).toBe('string');
      expect(card.card.openIssueHint).not.toBe(card.card.openCanvasNodeHint);
      expect(card.card.openIssueHint).not.toBe(card.card.openSourceHint);
    }
  });

  it('zh is a translation, not a copy of the English', () => {
    const en = load('en').outputs;
    const zh = load('zh').outputs;
    expect(zh.provenance).not.toBe(en.provenance);
    expect(zh.mentionEmpty).not.toBe(en.mentionEmpty);
    expect((zh.refError as unknown as Record<string, string>).unresolvable)
      .not.toBe((en.refError as unknown as Record<string, string>).unresolvable);
  });
});
