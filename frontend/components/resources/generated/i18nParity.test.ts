// frontend/components/resources/generated/i18nParity.test.ts
//
// en/zh parity for the Generated inbox namespaces.
//
// A grep over `t('...')` cannot do this job, and knowing why is the point of
// the file. Three of these namespaces are addressed by keys that are BUILT at
// runtime — `saveAsAsset.type.${value}`, `saveAsAsset.slot.${value}`,
// `generated.err.${code}` — so no literal for them exists anywhere in the
// source for a scanner to find. Worse, every one of those call sites passes an
// English fallback, so a missing zh entry is not an error, a warning, or a
// blank: a Chinese user just silently reads English. Nothing reports it.
//
// The slot list is taken from `assetSlots.ts` rather than restated here, so
// adding a slot fails this test until both locales carry a label for it.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { ASSET_TYPES, slotsFor } from '../../assets/assetSlots';

const LOCALES = path.resolve(__dirname, '../../../public/locales');

function load(lang: string): Record<string, unknown> {
  return JSON.parse(fs.readFileSync(path.join(LOCALES, `${lang}.json`), 'utf8'));
}

const en = load('en');
const zh = load('zh');

function at(tree: Record<string, unknown>, key: string): unknown {
  return key.split('.').reduce<unknown>(
    (node, part) =>
      node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined,
    tree,
  );
}

/** Every leaf path under `prefix`, so two trees can be compared as key SETS. */
function leaves(node: unknown, prefix: string, out: string[] = []): string[] {
  if (node && typeof node === 'object' && !Array.isArray(node)) {
    for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
      leaves(v, prefix ? `${prefix}.${k}` : k, out);
    }
  } else if (prefix) {
    out.push(prefix);
  }
  return out;
}

describe('Generated inbox i18n parity', () => {
  it.each(['generated', 'saveAsAsset'])('%s has the same keys in en and zh', (ns) => {
    const enKeys = leaves(at(en, ns), ns).sort();
    const zhKeys = leaves(at(zh, ns), ns).sort();
    expect(enKeys.length).toBeGreaterThan(0);
    expect(zhKeys).toEqual(enKeys);
  });

  it('the sidebar entry is translated', () => {
    expect(typeof at(en, 'resources.generated')).toBe('string');
    expect(typeof at(zh, 'resources.generated')).toBe('string');
  });

  it.each(ASSET_TYPES)('saveAsAsset.type.%s resolves in both locales', (type) => {
    expect(typeof at(en, `saveAsAsset.type.${type}`)).toBe('string');
    expect(typeof at(zh, `saveAsAsset.type.${type}`)).toBe('string');
  });

  it('every slot the picker can offer has a label in both locales', () => {
    // The union across types, `unsorted` included — `slotsFor` is what the
    // dialog renders from, so this is exactly the set a user can see.
    const slots = [...new Set(ASSET_TYPES.flatMap((type) => slotsFor(type)))];
    const missing = slots.flatMap((slot) => {
      const key = `saveAsAsset.slot.${slot}`;
      return [
        typeof at(en, key) === 'string' ? [] : [`en:${key}`],
        typeof at(zh, key) === 'string' ? [] : [`zh:${key}`],
      ].flat();
    });
    expect(missing).toEqual([]);
  });

  it('both error namespaces cover the same codes, generic included', () => {
    // `generic` is the fallback every unknown code lands on; without it a
    // failure the client cannot name renders as the raw key.
    for (const ns of ['generated.err', 'saveAsAsset.err']) {
      const enCodes = Object.keys(at(en, ns) as Record<string, unknown>).sort();
      const zhCodes = Object.keys(at(zh, ns) as Record<string, unknown>).sort();
      expect(enCodes).toContain('generic');
      expect(zhCodes).toEqual(enCodes);
    }
  });

  it('no zh value was left as its English source', () => {
    // A copy-paste of the English string is a silently untranslated key: it
    // reads as "done" to every check that only asks whether the key exists.
    // Short shared tokens are legitimately identical, so only compare the
    // longer prose.
    const same = leaves(at(en, 'generated'), 'generated')
      .filter((key) => {
        const e = at(en, key);
        return typeof e === 'string' && e.length > 12 && at(zh, key) === e;
      });
    expect(same).toEqual([]);
  });
});
