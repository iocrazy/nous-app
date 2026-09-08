// en/zh parity for the `question.*` namespace (QuestionCard and its mounts),
// scanned from source like prompts/promptsI18nParity.test.ts — a key added in
// one locale file only is invisible to English machines and silently English
// for zh users.
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const LOCALES = path.resolve(__dirname, '../../public/locales');
const ROOTS = [
  path.resolve(__dirname, '.'),
  path.resolve(__dirname, '../TaskCenter'),
  path.resolve(__dirname, '../chat'),
  path.resolve(__dirname, '..'),
];

function sources(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory()
      ? e.name === 'node_modules' ? [] : sources(path.join(dir, e.name))
      : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [path.join(dir, e.name)] : []);
}
function at(tree: Record<string, unknown>, key: string): unknown {
  return key.split('.').reduce<unknown>((n, p) => (n && typeof n === 'object' ? (n as Record<string, unknown>)[p] : undefined), tree);
}

describe('question.* locale parity', () => {
  const keys = new Set<string>();
  for (const f of new Set(ROOTS.flatMap(sources))) {
    for (const m of fs.readFileSync(f, 'utf8').matchAll(/t\(\s*'(question\.[A-Za-z0-9_.]+)'/g)) keys.add(m[1]);
  }
  const en = JSON.parse(fs.readFileSync(path.join(LOCALES, 'en.json'), 'utf8'));
  const zh = JSON.parse(fs.readFileSync(path.join(LOCALES, 'zh.json'), 'utf8'));
  it('finds keys to check', () => expect(keys.size).toBeGreaterThanOrEqual(5));
  it.each([...keys])('%s exists in en and zh', (key) => {
    expect(typeof at(en, key), `en ${key}`).toBe('string');
    expect(typeof at(zh, key), `zh ${key}`).toBe('string');
  });
});
