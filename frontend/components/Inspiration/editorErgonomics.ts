// Plain-textarea ports of memos' CodeMirror editing ergonomics (memos uses
// CodeMirror extensions; our quick-capture box keeps a native textarea).
//
// - Enter continues list items (todo / bullet / ordered); Enter on an EMPTY
//   item removes the marker and exits the list (memos listIndent behavior).
// - "[]" + space at line start becomes a todo marker (notion muscle memory).

const TODO_ITEM = /^(\s*)([-*+]) \[([ xX])\] (.*)$/;
const BULLET_ITEM = /^(\s*)([-*+]) (.*)$/;
const ORDERED_ITEM = /^(\s*)(\d+)([.)]) (.*)$/;

export interface EditResult {
  text: string;
  caret: number;
}

/** Current line's [start, end) around `pos` (end excludes the newline). */
function lineAt(text: string, pos: number): { start: number; end: number; line: string } {
  const start = text.lastIndexOf('\n', pos - 1) + 1;
  let end = text.indexOf('\n', pos);
  if (end === -1) end = text.length;
  return { start, end, line: text.slice(start, end) };
}

/** Enter pressed at `pos`: continue/exit list markers. Null = default enter. */
export function continueListOnEnter(text: string, pos: number): EditResult | null {
  const { start, end, line } = lineAt(text, pos);
  if (pos !== end) return null; // only when the caret sits at end-of-line

  const todo = line.match(TODO_ITEM);
  if (todo) {
    const [, indent, bullet, , content] = todo;
    if (!content.trim()) {
      // empty item → exit the list (drop the marker)
      return { text: text.slice(0, start) + text.slice(end), caret: start };
    }
    const insert = `\n${indent}${bullet} [ ] `;
    return { text: text.slice(0, pos) + insert + text.slice(pos), caret: pos + insert.length };
  }

  const ordered = line.match(ORDERED_ITEM);
  if (ordered) {
    const [, indent, num, sep, content] = ordered;
    if (!content.trim()) {
      return { text: text.slice(0, start) + text.slice(end), caret: start };
    }
    const insert = `\n${indent}${Number(num) + 1}${sep} `;
    return { text: text.slice(0, pos) + insert + text.slice(pos), caret: pos + insert.length };
  }

  const bullet = line.match(BULLET_ITEM);
  if (bullet) {
    const [, indent, mark, content] = bullet;
    if (!content.trim()) {
      return { text: text.slice(0, start) + text.slice(end), caret: start };
    }
    const insert = `\n${indent}${mark} `;
    return { text: text.slice(0, pos) + insert + text.slice(pos), caret: pos + insert.length };
  }

  return null;
}

/** Space pressed at `pos`: "[]" at line start becomes "- [ ] ". Null = default. */
export function todoShortcutOnSpace(text: string, pos: number): EditResult | null {
  const { start } = lineAt(text, pos);
  const before = text.slice(start, pos);
  const m = before.match(/^(\s*)\[\]$/);
  if (!m) return null;
  const replacement = `${m[1]}- [ ] `;
  return {
    text: text.slice(0, start) + replacement + text.slice(pos),
    caret: start + replacement.length,
  };
}
