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
