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
  // ⚠️ `palette/commands.ts` and `ui/canvasShortcuts.ts` were briefly listed
  // here, when OBJECT_TEXT below was file-gated. They are not any more, and
  // must not come back: both are ROW TABLES whose whole purpose is to grow, so
  // a file entry would wave through the next Library named from them. Their
  // two strings are allow-listed individually in ALLOWED_OBJECT_LABELS.
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

/** i18n defaults — the `t('some.key', 'English default')` form. USER_TEXT is
 *  structurally blind to these: its capture excludes quotes, so it dies at
 *  the key's opening quote. That blind spot is the whole ballgame here,
 *  because every label this task renamed IS a t() default — without this
 *  second pattern, putting `'Workflow Library'` back as the default at
 *  `smart/WorkflowLibraryPicker.tsx` leaves the suite green.
 *
 *  Only the two-string form is scanned. The `t(key, { defaultValue: … })`
 *  object form does exist in this tree (LibraryGrid, AssetNodeView,
 *  PromptMentionPicker, …), but no `defaultValue` string in canvas-core
 *  carries the word `library` — checked across the tree — so a branch for it
 *  would be reach with nothing behind it. Add one the day that stops being
 *  true. */
const T_DEFAULT = /\bt\(\s*'[^']*'\s*,\s*'([^']*)'/g;

/** The LABEL-TUPLE form — `['canvas.library.storeUploads', 'Files']` in a
 *  lookup table, resolved later as `t(key, english)`.
 *
 *  This is a THIRD blind spot, and the panel opened it. `canvas.library.*`
 *  keys must be single-quoted literals for `libraryI18n.test.ts` to see them,
 *  so a component with a chip row per store keeps its keys in a const map —
 *  which moves the English default out of `t()`'s literal form and straight
 *  past `T_DEFAULT` above. Every segment, scope and kind chip in the panel is
 *  written this way, so naming a second thing Library from a lookup table
 *  would otherwise leave the suite green.
 *
 *  NO label tuple carries the word today — `All Library` did until the review
 *  wave renamed it `Whole Workspace` (it collided with the in-library pill
 *  sitting in the same chip row). That is why the canary case below asserts
 *  the pattern still MATCHES PAIRS rather than matches offenders: a guard
 *  verified only by the thing it currently catches goes silent the day that
 *  thing is fixed, and this one has to survive the next chip map. */
const LABEL_TUPLE = /\[\s*'[^']*'\s*,\s*'([^']*)'\s*\]/g;

/** The OBJECT-PROPERTY form — `title: 'Add from library…'` in a command row,
 *  `label: 'Library panel'` in the shortcut table.
 *
 *  A FOURTH blind spot, and the widest one: USER_TEXT wants a JSX attribute
 *  (`title=`) or a text position, so a label declared as a bare object
 *  property is invisible to it, and the two patterns above want a `t()` call
 *  or a two-string tuple. Anything rendered from a const table of rows —
 *  the ⌘K palette, the shortcut help, any future menu — declares its user
 *  text exactly this way.
 *
 *  Same key set the JSX pattern already trusts as user-facing (`title`,
 *  `placeholder`, plus the `label` / `hint` these tables use), which is what
 *  keeps it off identifiers and code: a property name alone is not enough,
 *  the value has to be a quoted single-line string. Commented-out rows are
 *  dropped by `isCommentedOut` below — NOT by COMMENT_OPENER, which cannot
 *  help here: it tests whether the CAPTURED VALUE opens a comment, and in
 *  `// title: 'Old library'` the marker sits before the property, so the
 *  capture is the bare label and COMMENT_OPENER never fires. Verified by
 *  mutation rather than assumed.
 *
 *  Matched library-free and filtered by HAS_LIBRARY, like LABEL_TUPLE, so the
 *  canary below can count real matches instead of only the offender it
 *  currently knows about.
 *
 *  ALLOW-LISTED BY EXACT STRING, never by file — see ALLOWED_OBJECT_LABELS.
 *  The two files this pattern was written for are the ⌘K command table and the
 *  shortcut table, i.e. lists that exist in order to gain rows; exempting
 *  either wholesale is the failure mode ALLOWED_DEFAULTS' comment describes,
 *  and it was verified as real (adding a second `library` title to
 *  commands.ts left the suite green) before this gate was changed. */
const OBJECT_TEXT = /(?:title|label|hint|placeholder)\s*:\s*['"]([^'"\n]*)['"]/g;

const HAS_LIBRARY = /\blibrary\b/i;

/** The exact English defaults that genuinely name THE media library — the
 *  one thing the canvas is allowed to call Library. A default naming any
 *  OTHER library (a workflow store, a prompt-template store) does not belong
 *  here; it belongs in the rename table.
 *
 *  Allowlisted by exact string rather than by file on purpose: a file
 *  allowlist would wave through a NEW offender that happens to land in an
 *  already-listed file, which is precisely how the first version of this
 *  guard would have missed the five renames. Each entry says what it labels.
 *
 *  Every entry below still has at least one live mount. Task 7 deleted the
 *  add-reference popover, which held one of the two `Search Library…` mounts;
 *  `LibraryMediaPage.tsx` took over that string and the mention palette still
 *  carries the other. An entry that DID go dead would be
 *  harmless here, not a failure — the canary two cases below is what keeps
 *  the whole set from quietly emptying out. */
const ALLOWED_DEFAULTS = new Set([
  // Section label over the mention palette's Assets tab — one library per
  // canvas, so it names the media library rather than choosing between any.
  'Library',
  // Mention-palette pill restricting results to what is in that library.
  'In Library Only',
  // Search box of the media library (reference popover + mention palette).
  'Search Library…',
  // LibraryGrid's own search input, whose aria-label drops the ellipsis —
  // a ninth site, and a seventh distinct string, that the hand-built list
  // this allowlist started from did not have. The scan found it.
  'Search Library',
  // LibraryGrid's error state, for the media library it is rendering.
  'Could not load this library',
  // Assets-tab and asset-picker error state — the asset store OF that same
  // library (the picker mount is transitional; the palette mount stays).
  'Could not load the asset library',
  // OutputNodeView: this image has no generated-media record to save from.
  'This image has no library record to save',
  // The panel's Assets empty state, when the in-library pill is what emptied
  // it. Names THE library and the pill that narrows it — the pointed copy is
  // the only thing that makes that state recoverable.
  'No Library Members Here · turn off In Library Only to see everything',
  // Every prompt card's fixed header button, which opens THE panel. It is
  // deliberately not "Open Media Library" or "Open Assets": one library per
  // canvas is the whole point of this guard, so the bare word is the correct
  // name here rather than a disambiguation that would imply a second one.
  'Open Library',
]);

/** The exact row-table labels that genuinely name THE media library — the
 *  object-property counterpart of ALLOWED_DEFAULTS, and allow-listed the same
 *  way and for the same reason: by string, so a NEW offender landing in an
 *  already-listed file is still caught.
 *
 *  Each entry names the one panel the canvas is allowed to call Library. A
 *  label naming any OTHER library (a workflow store, a prompt-template store)
 *  does not belong here; it belongs in the rename table. */
/** Is the match at `index` inside a comment?
 *
 *  Looks at what precedes it ON ITS OWN LINE: a `//` anywhere before it, or a
 *  leading `*` (a JSDoc/block continuation line). That is the whole hazard for
 *  an object property — the marker is always to the left of the key, never
 *  inside the quoted value, which is why COMMENT_OPENER cannot do this job.
 *
 *  Cheap and line-local rather than a parse: this test reads source as text
 *  everywhere else too, and a false NEGATIVE here (a multi-line block comment
 *  whose inner lines carry no `*`) only costs a spurious offender someone then
 *  reads and fixes — never a missed one. */
function isCommentedOut(src: string, index: number): boolean {
  const lineStart = src.lastIndexOf('\n', index) + 1;
  const before = src.slice(lineStart, index);
  return before.includes('//') || /^\s*\*/.test(before);
}

const ALLOWED_OBJECT_LABELS = new Set([
  // palette/commands.ts — the ⌘K row that opens THE panel.
  'Add from library…',
  // ui/TopNodeBar.tsx — the chip in the node bar, same panel.
  'Library',
  // ui/canvasShortcuts.ts — the `?` help row documenting the L shortcut for
  // that same panel. Renaming it would make the shortcut help disagree with
  // the button it documents.
  'Library panel',
]);

/** All four patterns, over one file. */
function scan(file: string): { offenders: string[]; defaults: string[] } {
  const src = fs.readFileSync(file, 'utf8');
  const rel = path.relative(ROOT, file);
  const offenders: string[] = [];
  const defaults: string[] = [];

  if (!ALLOWED.has(file)) {
    for (const m of src.matchAll(USER_TEXT)) {
      const text = m[1].trim();
      if (COMMENT_OPENER.test(text)) continue;
      offenders.push(`${rel}: ${text}`);
    }
  }

  // OUTSIDE the ALLOWED branch on purpose: string-gated like the two below,
  // so a new Library named from a file that already names the right one is
  // still an offender.
  for (const m of src.matchAll(OBJECT_TEXT)) {
    const text = m[1].trim();
    if (isCommentedOut(src, m.index ?? 0)) continue;
    if (!HAS_LIBRARY.test(text)) continue;
    // Deliberately NOT pushed to `defaults`: that list feeds the t()-default
    // canary, and padding it with object labels would keep that canary green
    // even if T_DEFAULT stopped matching anything.
    if (!ALLOWED_OBJECT_LABELS.has(text)) {
      offenders.push(`${rel}: object label ${text}`);
    }
  }

  for (const pattern of [T_DEFAULT, LABEL_TUPLE]) {
    for (const m of src.matchAll(pattern)) {
      const text = m[1];
      if (!HAS_LIBRARY.test(text)) continue;
      defaults.push(`${rel}: ${text}`);
      if (!ALLOWED_DEFAULTS.has(text)) offenders.push(`${rel}: t() default ${text}`);
    }
  }

  return { offenders, defaults };
}

describe('the word Library on the canvas', () => {
  it('found files at all — an empty scan would pass every case below', () => {
    expect(tsFiles(ROOT).length).toBeGreaterThan(40);
  });

  it('the t() default pattern matches real code — an empty scan would too', () => {
    const found = tsFiles(ROOT).flatMap((f) => scan(f).defaults);
    expect(found.length).toBeGreaterThan(4);
  });

  it('the label-tuple pattern matches real code — the same canary, one level down', () => {
    // Without this, a typo in LABEL_TUPLE would silently stop scanning the
    // const maps every chip row in the panel is built from.
    const pairs = tsFiles(ROOT).flatMap((f) => [
      ...fs.readFileSync(f, 'utf8').matchAll(LABEL_TUPLE),
    ]);
    expect(pairs.length).toBeGreaterThan(20);
  });

  it('the object-property pattern matches real code — the canary one level further down', () => {
    // The ⌘K row this pattern was added for is the only library-bearing
    // object label outside the shortcut table, so counting offenders would
    // be a guard verified only by the thing it catches. Count every row
    // label instead: a typo in OBJECT_TEXT drops all of them.
    const labels = tsFiles(ROOT).flatMap((f) => [
      ...fs.readFileSync(f, 'utf8').matchAll(OBJECT_TEXT),
    ]);
    expect(labels.length).toBeGreaterThan(40);
  });

  it('names exactly one thing: the panel and its chip', () => {
    const offenders = tsFiles(ROOT).flatMap((f) => scan(f).offenders);
    expect(offenders).toEqual([]);
  });
});
