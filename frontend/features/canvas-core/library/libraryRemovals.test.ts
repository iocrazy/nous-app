// features/canvas-core/library/libraryRemovals.test.ts
//
// Spec §4 acceptance 9, for the components THIS plan retires. A deleted file
// with a surviving import is a build error, so what needs pinning is the
// opposite case: a file that quietly comes back, or an import that outlives
// its usefulness in a comment-shaped reference nobody notices.
//
// `AssetPromptPicker` and `MentionImageGrid` are NOT here: they are P3 and are
// still mounted (see the plan's Plan-time ruling 5). Asserting their absence
// now would be asserting a thing this plan deliberately does not do.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');

const GONE = [
  'smart/nodes/AssetPickerDialog.tsx',
  'smart/nodes/CanvasMentionPicker.tsx',
  'library/LibraryReferencePopover.tsx',
];

function tsFiles(dir: string): string[] {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .flatMap((e) =>
      e.isDirectory() ? tsFiles(path.join(dir, e.name)) : [path.join(dir, e.name)],
    )
    .filter((f) => /\.tsx?$/.test(f));
}

/**
 * This file names all three stems in `GONE` — it has to, that is the list —
 * so scanning it would make the guard permanently red about itself. Every
 * other file in the tree is fair game.
 */
const scanned = () => tsFiles(ROOT).filter((f) => f !== __filename);

describe('components this plan retires', () => {
  it.each(GONE)('%s is gone', (rel) => {
    expect(fs.existsSync(path.join(ROOT, rel))).toBe(false);
  });

  it.each(GONE)('nothing still names %s', (rel) => {
    const stem = path.basename(rel).replace(/\.tsx?$/, '');
    // WHOLE WORD, not `includes`. `useCanvasMentionPicker` is a live hook
    // whose name CONTAINS `CanvasMentionPicker`, and so does every file that
    // imports it — a substring match would report the hook's own callers as
    // ghosts of the deleted dialog and there would be no way to make it green
    // except by renaming a component that is doing nothing wrong.
    const named = new RegExp(`\\b${stem}\\b`);
    const hits = scanned().filter((f) => named.test(fs.readFileSync(f, 'utf8')));
    expect(hits.map((f) => path.relative(ROOT, f))).toEqual([]);
  });

  it('found files at all — an empty scan would pass every case above', () => {
    expect(scanned().length).toBeGreaterThan(40);
  });
});
