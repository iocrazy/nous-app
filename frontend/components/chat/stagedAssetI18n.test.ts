// components/chat/stagedAssetI18n.test.ts
//
// The loadout menu's three strings are asserted here against BOTH locale
// files, by value. `StagedAssetLoadoutMenu.test.tsx` resolves its mocked `t`
// through `en.json`, but two of the three keys have a `defaultValue` that is
// byte-identical to the shipped copy, so deleting those keys would leave that
// suite green; and nothing under `chat.*` is covered by the asset-library
// parity test. This file is what turns a lost key red.

import { describe, expect, it } from 'vitest';

import en from '../../public/locales/en.json';
import zh from '../../public/locales/zh.json';

const read = (tree: unknown, key: string): unknown =>
  key.split('.').reduce<unknown>(
    (node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined),
    tree,
  );

const KEYS = {
  'chat.stagedAsset.chooseLoadout': { en: 'Choose Loadout', zh: '选择造型' },
  'chat.stagedAsset.defaultLoadout': { en: 'Default Loadout', zh: '默认造型' },
  'chat.stagedAsset.loadoutsUnavailable': { en: 'Could Not Load Loadouts', zh: '无法加载造型' },
} as const;

describe('staged asset loadout menu copy', () => {
  it.each(Object.entries(KEYS))('%s ships in both locales with the pinned copy', (key, want) => {
    expect(read(en, key)).toBe(want.en);
    expect(read(zh, key)).toBe(want.zh);
  });

  it('every English value is Title Case (CLAUDE.md UI copy rule)', () => {
    for (const { en: value } of Object.values(KEYS)) {
      for (const word of value.split(' ')) expect(word[0]).toBe(word[0].toUpperCase());
    }
  });
});
