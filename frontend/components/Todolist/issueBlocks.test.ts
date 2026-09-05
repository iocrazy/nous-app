import { afterEach, describe, expect, it } from 'vitest';

import { __resetIssueBlocks, blocksFor, issueBlockContext, registerIssueBlock, registeredIssueBlocks } from './issueBlocks';

const Noop = () => null;

afterEach(() => __resetIssueBlocks());

describe('issueBlocks registry', () => {
  it('picks blocks by zone + match, ordered, enumerable, and never lets a broken matcher hide the page', () => {
    registerIssueBlock({ id: 'budget', zone: 'cockpit', order: 30, match: () => true, component: Noop });
    registerIssueBlock({ id: 'steps', zone: 'cockpit', order: 10, match: () => true, component: Noop });
    registerIssueBlock({ id: 'publish-ctx', zone: 'context', order: 10, match: (c) => c.originKind === 'publish', component: Noop });
    registerIssueBlock({ id: 'broken', zone: 'cockpit', order: 0, match: () => { throw new Error('x'); }, component: Noop });
    const ctx = issueBlockContext({ id: 1, origin_kind: 'manual' }, { phase: 'running', origin: { kind: 'publish' } });
    expect(ctx).toMatchObject({ originKind: 'publish', phase: 'running' });
    expect(blocksFor('cockpit', ctx).map((b) => b.id)).toEqual(['steps', 'budget']);
    expect(blocksFor('context', ctx).map((b) => b.id)).toEqual(['publish-ctx']);
    expect(blocksFor('context', issueBlockContext({ origin_kind: 'manual' }, null)).map((b) => b.id)).toEqual([]);
    expect(registeredIssueBlocks()).toEqual(['broken', 'budget', 'publish-ctx', 'steps']);
    expect(() => registerIssueBlock({ id: 'steps', zone: 'cockpit', order: 1, match: () => true, component: Noop })).toThrow();
  });
});
