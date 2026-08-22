// features/canvas-core/smart/promptSplit.ts
//
// IC 分隔符拆分 (smart-canvas.js promptNodePromptItems): one prompt node
// fans out into N independent generation items, split on a user separator
// (default ';', max 8 chars). A split that yields fewer than 2 items falls
// back to the whole text — the feature never silently loses content.

export const DEFAULT_SPLIT_SEPARATOR = ';';
export const MAX_SEPARATOR_LENGTH = 8;

export function splitPromptItems(body: string, separator: string): string[] {
  const text = (body ?? '').trim();
  if (!text) return [];
  const sep = (separator ?? '').slice(0, MAX_SEPARATOR_LENGTH);
  if (!sep) return [text];
  const items = text
    .split(sep)
    .map((s) => s.trim())
    .filter(Boolean);
  return items.length > 1 ? items : [text];
}
