#!/usr/bin/env node
// zinc→ink codemod (island redesign v2 P1a).
// Mechanically renames Tailwind `*-zinc-N` utilities to `*-ink-N` inside
// string literals of .tsx/.ts files. Variants (hover:, md:, group-hover:)
// and opacity modifiers (/50) are untouched because we only rewrite the
// `zinc-<shade>` segment when preceded by a known utility prefix.
//
// Usage:
//   node scripts/migrate-ink.mjs --dry <dir|file>...   # report only
//   node scripts/migrate-ink.mjs <dir|file>...         # rewrite in place
//   node scripts/migrate-ink.mjs --self-test
import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';

// Every Tailwind color-utility prefix observed in this repo. `zinc-` only
// rewrites when attached to one of these, so prose like "zinc-alloy" is safe.
const PREFIXES = [
  'bg', 'text', 'border', 'border-t', 'border-b', 'border-l', 'border-r',
  'border-x', 'border-y', 'ring', 'ring-offset', 'divide', 'placeholder',
  'from', 'via', 'to', 'shadow', 'outline', 'decoration', 'accent', 'caret',
  'fill', 'stroke',
];
const RE = new RegExp(
  `(\\b(?:${PREFIXES.map(p => p.replace(/-/g, '\\-')).join('|')})-)zinc(-(?:50|100|200|300|400|500|600|700|800|900|950)\\b)`,
  'g',
);

function transform(src) {
  let count = 0;
  const out = src.replace(RE, (_, pre, shade) => { count++; return `${pre}ink${shade}`; });
  return { out, count };
}

function* walk(path) {
  const st = statSync(path);
  if (st.isFile()) { yield path; return; }
  for (const name of readdirSync(path)) {
    if (name === 'node_modules' || name === 'dist') continue;
    yield* walk(join(path, name));
  }
}

function selfTest() {
  const cases = [
    ['bg-zinc-800', 'bg-ink-800'],
    ['hover:bg-zinc-800/50', 'hover:bg-ink-800/50'],
    ['md:text-zinc-400 group-hover:border-zinc-700/60', 'md:text-ink-400 group-hover:border-ink-700/60'],
    ['from-zinc-900 via-zinc-800 to-zinc-950', 'from-ink-900 via-ink-800 to-ink-950'],
    ['divide-zinc-800 ring-zinc-700 placeholder-zinc-600', 'divide-ink-800 ring-ink-700 placeholder-ink-600'],
    ['border-t-zinc-800 border-x-zinc-700', 'border-t-ink-800 border-x-ink-700'],
    // must NOT touch:
    ['zinc-800', 'zinc-800'],                  // bare token (no utility prefix)
    ['bg-gray-800 text-indigo-400', 'bg-gray-800 text-indigo-400'],
    ['bgzinc-800', 'bgzinc-800'],              // not a word boundary
  ];
  let failed = 0;
  for (const [input, expected] of cases) {
    const { out } = transform(input);
    if (out !== expected) { failed++; console.error(`FAIL: ${input} -> ${out} (want ${expected})`); }
  }
  console.log(failed === 0 ? `self-test OK (${cases.length} cases)` : `self-test FAILED (${failed})`);
  process.exit(failed === 0 ? 0 : 1);
}

const args = process.argv.slice(2);
if (args.includes('--self-test')) selfTest();
const dry = args.includes('--dry');
const targets = args.filter(a => !a.startsWith('--'));
if (targets.length === 0) { console.error('usage: migrate-ink.mjs [--dry] <dir|file>...'); process.exit(1); }

let totalFiles = 0, totalRepl = 0;
for (const target of targets) {
  for (const file of walk(target)) {
    if (!['.tsx', '.ts'].includes(extname(file))) continue;
    const src = readFileSync(file, 'utf8');
    const { out, count } = transform(src);
    if (count === 0) continue;
    totalFiles++; totalRepl += count;
    if (dry) console.log(`${file}: ${count}`);
    else writeFileSync(file, out);
  }
}
console.log(`${dry ? '[dry] ' : ''}${totalRepl} replacements in ${totalFiles} files`);
