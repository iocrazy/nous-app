// features/canvas-core/library/libraryNaming.test.ts
//
// Spec §4 acceptance 1: on the canvas, "Library" names ONE thing.
//
// Four different libraries used to answer to that word, so a user could not
// tell from the label which one a button would open. This scans the canvas
// source for the word in user-visible positions and refuses any occurrence
// outside the allowlist below.
//
// It scans SOURCE rather than a rendered tree on purpose: the strings sit in
// five components with five different mounting conditions, and a render test
// would need all five staged to notice a regression in any one.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');

/** Files allowed to put the word Library in front of a user. */
const ALLOWED = new Set([
  path.join(ROOT, 'library', 'LibraryPanel.tsx'),
  path.join(ROOT, 'ui', 'TopNodeBar.tsx'),
]);

function tsFiles(dir: string): string[] {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .flatMap((e) =>
      e.isDirectory()
        ? tsFiles(path.join(dir, e.name))
        : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)
          ? [path.join(dir, e.name)]
          : [],
    );
}

/** Quoted strings and JSX text — where a USER would read it. Import
 *  specifiers, identifiers and comments are none of this test's business.
 *
 *  `\blibrary\b` with the `i` flag rather than a bare `Library`: the bare
 *  form both over- and under-matches. It matches identifiers that happen to
 *  follow an arrow (`() => setLibraryOpen(...)`, `useLibraryStore.getState()`)
 *  — code, not user text, and the rename cannot make those go away — while
 *  missing the lowercase `Load from library` that this rename targets. Word
 *  boundaries drop `setLibraryOpen`/`LibraryPanel`; case-insensitivity keeps
 *  the lowercase prose. */
const USER_TEXT =
  /(?:aria-label=|title=|placeholder=|>)\s*[{"']?\s*([^"'<>{}\n]*\blibrary\b[^"'<>{}\n]*)/gi;

// A JSX comment opens with a brace, so a tag's closing `>` followed by a
// newline and a braced comment is indistinguishable from JSX text to the
// pattern above — it captured `One library per canvas — the route` (a
// comment) out of PromptMentionPicker. The pattern's own contract already
// says comments are not its business, so drop a capture that IS the opening
// of one. This can only remove false positives: no label a user reads
// begins with a comment marker.
const COMMENT_OPENER = /^\/[*/]/;

describe('the word Library on the canvas', () => {
  it('found files at all — an empty scan would pass every case below', () => {
    expect(tsFiles(ROOT).length).toBeGreaterThan(40);
  });

  it('names exactly one thing: the panel and its chip', () => {
    const offenders: string[] = [];
    for (const file of tsFiles(ROOT)) {
      if (ALLOWED.has(file)) continue;
      const src = fs.readFileSync(file, 'utf8');
      for (const m of src.matchAll(USER_TEXT)) {
        const text = m[1].trim();
        if (COMMENT_OPENER.test(text)) continue;
        offenders.push(`${path.relative(ROOT, file)}: ${text}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
