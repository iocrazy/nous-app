/**
 * API client for user custom beat templates (Beats M3.5).
 *
 * A custom template is a `beat_templates` row owned by the caller: a name plus
 * an ordered list of percentage anchors (title / summary / color + pctStart /
 * pctEnd). The backend enforces ownership; this client just speaks the
 * { success, data } envelope. Snowflake ids come back as JSON numbers, so the
 * row id is coerced to a string at the boundary (never Number()-compare / lose
 * precision), matching the beats/scenes convention.
 */
import { getApiUrl } from '../../utils/apiConfig';
import { getAuthHeaders } from '../../services/parserService';
import { unwrapResponse } from '../../utils/apiHelpers';
import type { CustomTemplateAnchor } from './templates';

const apiBase = () => `${getApiUrl()}/api/v1`;

export interface CustomTemplate {
  id: string;
  name: string;
  anchors: CustomTemplateAnchor[];
}

type CustomTemplateRow = Omit<CustomTemplate, 'id'> & { id: string | number };

/** Coerce the snowflake id to a string (JSON number → precision-safe string). */
function toCustomTemplate(row: CustomTemplateRow): CustomTemplate {
  return { ...row, id: String(row.id) };
}

export async function listBeatTemplates(): Promise<CustomTemplate[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/beat-templates`, { headers });
  const rows = await unwrapResponse<CustomTemplateRow[]>(res);
  return rows.map(toCustomTemplate);
}

export async function createBeatTemplate(
  name: string,
  anchors: CustomTemplateAnchor[],
): Promise<CustomTemplate> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/beat-templates`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, anchors }),
  });
  return toCustomTemplate(await unwrapResponse<CustomTemplateRow>(res));
}

export async function renameBeatTemplate(
  templateId: string,
  name: string,
): Promise<CustomTemplate> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/beat-templates/${templateId}`, {
    method: 'PUT',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  return toCustomTemplate(await unwrapResponse<CustomTemplateRow>(res));
}

export async function deleteBeatTemplate(templateId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${apiBase()}/beat-templates/${templateId}`, { method: 'DELETE', headers });
}
