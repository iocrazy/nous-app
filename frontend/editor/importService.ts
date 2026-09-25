/**
 * Screenplay import service (spec v3 import — v2 editor).
 *
 * Talks to the async `POST /scripts/import-screenplay` endpoint, which creates
 * a NEW script from imported text and dispatches the `script_import` workflow.
 * (The legacy `POST /scripts/import` multipart doc→chapters route of the old
 * editor had no caller left and was removed in OpenAPI P6.)
 */
import { getAuthHeaders } from '../services/parserService';
import { getApiUrl } from '../utils/apiConfig';
import { handleResponse } from '../utils/apiHelpers';
import type { ScriptImportScreenplayDispatch } from '../types/api';

/** Upper bound on imported text length (chars) — mirrors the backend 1MB gate. */
export const MAX_IMPORT_CHARS = 1024 * 1024;

export type ImportMode = 'fountain' | 'prose';

/** `{ success, script_id, task_id }`: poll `task_id`, then open `script_id`. */
export type ImportScreenplayResult = ScriptImportScreenplayDispatch;

// Line-start tokens that mark a Fountain scene heading (mirrors the backend
// parser's prefixes) — used only to auto-suggest the mode, never authoritative.
const HEADING_PREFIXES = [
  'INT.',
  'EXT.',
  'EST.',
  'INT/EXT',
  'EXT/INT',
  'I/E',
  'INT ',
  'EXT ',
];

/**
 * Suggest an import mode from the text: two or more line-start scene headings
 * (INT./EXT./EST. …) reads as Fountain; anything else is treated as prose. Pure
 * + deterministic (exhaustively unit-tested); the user can always override.
 */
export function detectImportMode(text: string): ImportMode {
  let headings = 0;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trimStart().toUpperCase();
    if (HEADING_PREFIXES.some((prefix) => line.startsWith(prefix))) {
      headings += 1;
      if (headings >= 2) return 'fountain';
    }
  }
  return 'prose';
}

export async function importScreenplay(data: {
  project_id: string;
  name: string;
  mode: ImportMode;
  content: string;
}): Promise<ImportScreenplayResult> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/import-screenplay`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return handleResponse<ImportScreenplayResult>(res);
}
