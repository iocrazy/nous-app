// components/Distribution/CoverStudio/promptMentions.ts
//
// "@" a picture in the prompt box. The user types `@` and picks one of the
// pictures being sent to the model (or a template, which is then added to
// the pool); the box shows a `@{label}` token. Before the prompt goes out the
// tokens are expanded into what the model can act on — "reference image 3
// (Krea poster)" — because the model receives images by POSITION, not by our
// labels. Tokens for pictures no longer in the pool are left as plain text so
// nothing silently disappears from what the user wrote.

import type { CoverReference } from './coverReferences';
import { formatTimestamp } from './coverReferences';

export const MENTION_RE = /@\{([^}]+)\}/g;

/**
 * A filename as a label: no extension, no braces, and short — uploads often
 * arrive as 40-character hashes, which overflow the pool captions and turn a
 * mention into a wall of hex.
 */
export function shortName(raw: string, max = 18): string {
  const base = (raw || '').replace(/\.[^./\\]+$/, '').replace(/[{}]/g, '').trim();
  if (base.length <= max) return base || 'picture';
  return `${base.slice(0, max - 1)}…`;
}

/** The label a reference is mentioned by — stable, human, unique enough. */
export function mentionLabel(ref: CoverReference): string {
  if (ref.kind === 'person') return 'person';
  if (ref.kind === 'frame') {
    return `frame ${typeof ref.timestampSeconds === 'number' ? formatTimestamp(ref.timestampSeconds) : ''}`.trim();
  }
  return shortName(ref.label || 'template');
}

export function mentionToken(ref: CoverReference): string {
  return `@{${mentionLabel(ref)}}`;
}

/** Insert a token at the caret, replacing the `@…` the user was typing. */
export function insertMention(
  text: string,
  caret: number,
  token: string,
): { text: string; caret: number } {
  // The trigger is the last `@` before the caret that is not already a
  // finished token; whatever follows it is the partial query being replaced.
  const before = text.slice(0, caret);
  const at = before.lastIndexOf('@');
  const start = at >= 0 && !before.slice(at).includes('}') ? at : caret;
  const next = `${text.slice(0, start)}${token} ${text.slice(caret)}`;
  return { text: next, caret: start + token.length + 1 };
}

/**
 * Replace `@{label}` with the model-facing reference, by position in the
 * list the server sends (person first — see `sourceUrls`).
 */
export function expandMentions(text: string, orderedRefs: CoverReference[]): string {
  if (!text) return text;
  const byLabel = new Map<string, number>();
  orderedRefs.forEach((r, i) => {
    const label = mentionLabel(r);
    if (!byLabel.has(label)) byLabel.set(label, i + 1);
  });
  return text.replace(MENTION_RE, (whole, label: string) => {
    const n = byLabel.get(label.trim());
    return n ? `reference image ${n} (${label.trim()})` : whole;
  });
}
