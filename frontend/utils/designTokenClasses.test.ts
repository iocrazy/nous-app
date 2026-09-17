/**
 * Every class that names one of OUR colour families must name a token that
 * exists.
 *
 * Tailwind v4 emits nothing for an undefined colour — no warning, no build
 * error. The class sits in the markup looking perfectly reasonable and styles
 * nothing. Two of these reached users:
 *
 *   `bg-island-1`   the Transcript toolbar's sticky backdrop. There is no
 *                   `island-1` (only `island` and `island-2`), so the row was
 *                   transparent and the segments showed straight through the
 *                   controls as they scrolled under them.
 *   `text-warn-600` a notice written with a Tailwind-palette habit. The warn
 *                   family is `warn` / `warn-soft` / `warn-line`; `warn-600`
 *                   rendered in the inherited colour.
 *
 * Neither fails a type check, a lint, a unit test, or a build. Checking the
 * class against the token list is the only place this can be caught.
 *
 * Scope is deliberately the families this design system DEFINES ITSELF
 * (see `@theme` in index.css). Tailwind's own palette (`red-500`, `black/80`,
 * …) always exists, so scanning it would only produce noise.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = join(__dirname, '..');

/** Colour families declared by this app's @theme, with no Tailwind default. */
const OWN_FAMILIES = ['island', 'ok', 'warn', 'danger', 'info', 'agent'];

/** Utilities that take a colour. */
const COLOR_UTILITIES = [
  'bg', 'text', 'border', 'ring', 'from', 'via', 'to', 'fill', 'stroke',
  'outline', 'divide', 'placeholder', 'decoration', 'caret', 'shadow',
  'ring-offset', 'accent',
];

const definedTokens = (): Set<string> => {
  const css = readFileSync(join(ROOT, 'index.css'), 'utf8');
  const out = new Set<string>();
  for (const m of css.matchAll(/--color-([a-z0-9-]+)\s*:/g)) out.add(m[1]);
  return out;
};

const walk = (dir: string, acc: string[] = []): string[] => {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === 'dist' || name.startsWith('.')) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, acc);
    else if (/\.(tsx|ts)$/.test(name) && !/\.test\.tsx?$/.test(name)) acc.push(full);
  }
  return acc;
};

const classPattern = new RegExp(
  // variant prefixes (hover:, md:, dark:, group-hover:, …) are allowed before
  String.raw`(?:^|[\s"'\`:])(?:${COLOR_UTILITIES.join('|')})-((?:${OWN_FAMILIES.join('|')})(?:-[a-z0-9]+)?)(?:\/\d+)?(?=[\s"'\`]|$)`,
  'g',
);

describe('design-token colour classes', () => {
  const tokens = definedTokens();

  it('found the token list at all', () => {
    // A broken parse would make every assertion below pass vacuously.
    expect(tokens.has('island')).toBe(true);
    expect(tokens.has('warn')).toBe(true);
  });

  it('only references colour tokens that exist', () => {
    const problems: string[] = [];
    for (const file of [
      ...walk(join(ROOT, 'components')),
      ...walk(join(ROOT, 'pages')),
      ...walk(join(ROOT, 'hooks')),
    ]) {
      const src = readFileSync(file, 'utf8');
      for (const m of src.matchAll(classPattern)) {
        const token = m[1];
        if (!tokens.has(token)) {
          const line = src.slice(0, m.index).split('\n').length;
          problems.push(`${relative(ROOT, file)}:${line}  ${m[0].trim()}  (no --color-${token})`);
        }
      }
    }
    expect(problems, problems.join('\n')).toEqual([]);
  });

  it('catches the two that shipped', () => {
    // Guard the guard: both real defects must trip the pattern.
    const sample = `className="sticky bg-island-1" className="text-warn-600 text-xs"`;
    const hits = [...sample.matchAll(classPattern)].map((m) => m[1]);
    expect(hits).toEqual(['island-1', 'warn-600']);
    expect(tokens.has('island-1')).toBe(false);
    expect(tokens.has('warn-600')).toBe(false);
  });
});
