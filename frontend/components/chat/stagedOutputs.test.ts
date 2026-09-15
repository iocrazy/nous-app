/**
 * Staged CITATIONS — the composer's holding area for picked output versions.
 *
 * Their own list beside the staged files and assets for the reason the assets
 * list is separate from the resources one: a citation resolves through a
 * different backend path (`output_ref_resolver`, which validates ISSUE
 * ownership) and carries no bytes at all. Folding it in would make every
 * consumer re-derive which kind it holds.
 */
import { describe, expect, it } from 'vitest';

import {
  removeStagedOutput,
  stageOutput,
  toOutputAttachment,
  type StagedOutputRef,
} from './stagedOutputs';
import { MAX_OUTPUT_REF_ATTACHMENTS } from './attachmentLimits';

const pick = (version: number, refId = '727145299382534999'): Omit<StagedOutputRef, never> => ({
  ref_kind: 'script_shot',
  ref_id: refId,
  version,
  title: `S3 · Shot #1`,
});

describe('stageOutput', () => {
  it('appends a citation', () => {
    expect(stageOutput([], pick(3)).list).toEqual([pick(3)]);
  });

  it('treats the same object at a different version as a second citation', () => {
    // "v1 → v3, look what changed" is one comment with two citations. Keying
    // on the object alone would silently replace the first pick.
    const list = stageOutput(stageOutput([], pick(3)).list, pick(1)).list;
    expect(list.map((s) => s.version)).toEqual([3, 1]);
  });

  it('ignores a repeat of the same version — a double-click is one intent', () => {
    const once = stageOutput([], pick(3)).list;
    expect(stageOutput(once, pick(3)).list).toBe(once);
  });

  it('refuses a citation with no ref_id rather than staging a chip that cannot resolve', () => {
    const res = stageOutput([], { ...pick(1), ref_id: '' });
    expect(res.list).toEqual([]);
  });

  it('B6: names the coordinate-less refusal, so the caller cannot read it as «already staged»', () => {
    // 原样返回列表是「这条早就在里面了，没什么可说的」的答案。坐标缺失是另一
    // 件事：什么都没暂存，而调用方本该说出来（本仓「触发路径必须类型化失败
    // 回显」）。两者共用一个返回值，调用方就分不清。
    expect(stageOutput([], { ...pick(1), ref_id: '' }).outcome).toBe('unusable');
    expect(stageOutput([], { ...pick(1), ref_kind: '' }).outcome).toBe('unusable');
    expect(stageOutput([], { ...pick(1), version: Number.NaN }).outcome).toBe('unusable');
    const once = stageOutput([], pick(3));
    expect(once.outcome).toBe('staged');
    expect(stageOutput(once.list, pick(3)).outcome).toBe('duplicate');
  });

  it('never mutates the list it was given', () => {
    const before = stageOutput([], pick(3)).list;
    const snapshot = [...before];
    stageOutput(before, pick(2));
    expect(before).toEqual(snapshot);
  });

  it('answers «limit» past the cap instead of silently dropping the pick', () => {
    // The server refuses the WHOLE comment past this cap, so a ninth chip that
    // looked staged would produce a comment that never posts. The `limit`
    // outcome is what lets the composer say so.
    let list: StagedOutputRef[] = [];
    for (let i = 1; i <= MAX_OUTPUT_REF_ATTACHMENTS; i += 1) {
      list = stageOutput(list, pick(1, `72714529938253${4000 + i}`)).list;
    }
    expect(list).toHaveLength(MAX_OUTPUT_REF_ATTACHMENTS);
    expect(stageOutput(list, pick(1, '727145299382539999')).outcome).toBe('limit');
  });

  it('lets a duplicate through at the cap — it adds nothing to refuse', () => {
    let list: StagedOutputRef[] = [];
    for (let i = 1; i <= MAX_OUTPUT_REF_ATTACHMENTS; i += 1) {
      list = stageOutput(list, pick(1, `72714529938253${4000 + i}`)).list;
    }
    // Re-picking one already staged must not be reported as "over the limit":
    // nothing would be added, and the message would name a cap the user has
    // not actually exceeded.
    expect(stageOutput(list, pick(1, '727145299382534001')).outcome).toBe('duplicate');
  });
});

describe('removeStagedOutput', () => {
  it('drops exactly the cited version, leaving the object’s other citation', () => {
    const list = stageOutput(stageOutput([], pick(3)).list, pick(1)).list;
    const next = removeStagedOutput(list, 'script_shot', '727145299382534999', 3);
    expect(next.map((s) => s.version)).toEqual([1]);
  });
});

describe('toOutputAttachment', () => {
  it('emits the five wire keys and nothing composer-side', () => {
    expect(toOutputAttachment(pick(2))).toEqual({
      kind: 'output_ref',
      ref_kind: 'script_shot',
      ref_id: '727145299382534999',
      version: 2,
      title: 'S3 · Shot #1',
    });
  });

  it('sends a null title rather than an empty string', () => {
    // `''` reads as "the title is the empty string"; the registry's absent
    // title is null, and the resolver overwrites it anyway.
    expect(toOutputAttachment({ ...pick(1), title: null })).toMatchObject({ title: null });
  });
});
