// frontend/services/coverTemplateService.ts
//
// Cover Studio's 样图模板库 (migration 435). A template is a picture you liked,
// kept as an example and handed to the model as a reference image. It does not
// replace the style, which is text and lives in `skills`.
//
// Both ways of adding one are two calls, not one, and deliberately so:
//
//   upload      importCanvasMedia(file)        → generated_media id
//   from library importResourceAsCanvasMedia(resourceId) → generated_media id
//                                 ↓
//                    createCoverTemplate({ generated_media_id, name })
//
// The detour through /generated-media exists because the image generation
// bridge only accepts `/api/v1/generated-media/{id}/(cover|stream|file)` URLs
// as references — anything else is dropped by `generated_media_local_path()`
// with no error at all. Storing a resource id here instead would produce a
// template that appears to work, generates, and simply is not in the picture.

import { apiFetch } from './apiClient';
import {
  importCanvasMedia,
  importResourceAsCanvasMedia,
} from '../features/canvas-core/smart/mediaImport';

/** Wire shape of a cover template. Every id is a STRING (snowflake > 2^53). */
export interface CoverTemplate {
  id: string;
  name: string;
  generated_media_id: string;
  /** Already a valid <img src> AND a valid generation reference URL. */
  image_url: string;
  source_kind: 'upload' | 'library' | 'generated';
  source_resource_id: string | null;
  usage_count: number;
  last_used_at: string | null;
  created_at: string;
}

export type CoverTemplateFailure =
  | 'not-found'
  | 'not-an-image'
  | 'forbidden'
  | 'unauthenticated'
  | 'server'
  | 'network';

/**
 * Typed failure. Throwing rather than returning a result union is deliberate:
 * a caller that forgets to branch gets a loud rejection, never a silent no-op
 * — the same reasoning as GeneratedMediaError.
 */
export class CoverTemplateError extends Error {
  readonly failure: CoverTemplateFailure;
  readonly status?: number;

  constructor(failure: CoverTemplateFailure, message: string, status?: number) {
    super(message);
    this.name = 'CoverTemplateError';
    this.failure = failure;
    this.status = status;
  }
}

function failureFromStatus(status: number): CoverTemplateFailure {
  if (status === 404) return 'not-found';
  if (status === 400) return 'not-an-image';
  if (status === 401) return 'unauthenticated';
  if (status === 403) return 'forbidden';
  return 'server';
}

async function call<T>(path: string, init?: Parameters<typeof apiFetch>[1]): Promise<T> {
  let response: Response;
  try {
    response = await apiFetch(path, init);
  } catch (err) {
    // apiFetch throws ApiError for non-2xx and a plain error for transport
    // failures. Both land here; `status` tells them apart.
    const status = (err as { status?: number })?.status;
    if (typeof status === 'number') {
      throw new CoverTemplateError(
        failureFromStatus(status),
        (err as Error).message || 'cover template request failed',
        status,
      );
    }
    throw new CoverTemplateError('network', (err as Error)?.message || 'network error');
  }
  const json = (await response.json()) as { data?: T };
  if (json?.data === undefined) {
    throw new CoverTemplateError('server', 'response carried no data');
  }
  return json.data;
}

export async function listCoverTemplates(): Promise<CoverTemplate[]> {
  const data = await call<{ items: CoverTemplate[] }>('/api/v1/cover-templates');
  return data.items ?? [];
}

export async function createCoverTemplate(input: {
  generatedMediaId: string;
  name: string;
  sourceKind?: CoverTemplate['source_kind'];
  sourceResourceId?: string | null;
}): Promise<CoverTemplate> {
  return call<CoverTemplate>('/api/v1/cover-templates', {
    method: 'POST',
    json: {
      generated_media_id: input.generatedMediaId,
      name: input.name,
      source_kind: input.sourceKind ?? 'upload',
      source_resource_id: input.sourceResourceId ?? null,
    },
  });
}

export async function renameCoverTemplate(
  templateId: string,
  name: string,
): Promise<CoverTemplate> {
  return call<CoverTemplate>(`/api/v1/cover-templates/${templateId}`, {
    method: 'PATCH',
    json: { name },
  });
}

export async function deleteCoverTemplate(templateId: string): Promise<boolean> {
  const data = await call<{ deleted: boolean }>(
    `/api/v1/cover-templates/${templateId}`,
    { method: 'DELETE' },
  );
  return Boolean(data.deleted);
}

/**
 * Bump "used N×" after a generation was dispatched with these templates.
 *
 * Fire-and-forget on purpose: the count drives sort order and a caption, so a
 * lost tick is cosmetic, and it must never be able to fail the generation the
 * user actually asked for. Errors are logged, never rethrown — the one place
 * in this module where swallowing is the correct behaviour, and the reason is
 * written here rather than left to the reader.
 */
export async function markCoverTemplatesUsed(templateIds: string[]): Promise<void> {
  if (templateIds.length === 0) return;
  try {
    await call<{ counted: number }>('/api/v1/cover-templates/use', {
      method: 'POST',
      json: { template_ids: templateIds },
    });
  } catch (err) {
    console.error('[coverTemplateService] markCoverTemplatesUsed failed:', err);
  }
}

/** Upload a local picture and save it as a template, in that order. */
export async function addCoverTemplateFromFile(
  file: File,
  name: string,
): Promise<CoverTemplate> {
  const ref = await importCanvasMedia(file, null, null);
  if (ref.kind !== 'image') {
    throw new CoverTemplateError('not-an-image', 'a cover template must be an image');
  }
  if (!ref.id) {
    // The backend does return it; a missing id means the contract moved.
    // Failing loudly beats saving a template that points nowhere.
    throw new CoverTemplateError('server', 'import returned no generated-media id');
  }
  return createCoverTemplate({
    generatedMediaId: ref.id,
    name,
    sourceKind: 'upload',
  });
}

/** Take a picture already in the resource library and save it as a template. */
export async function addCoverTemplateFromResource(
  resourceId: string,
  name: string,
): Promise<CoverTemplate> {
  const ref = await importResourceAsCanvasMedia(resourceId);
  if (ref.kind !== 'image') {
    throw new CoverTemplateError('not-an-image', 'a cover template must be an image');
  }
  if (!ref.id) {
    throw new CoverTemplateError('server', 'import returned no generated-media id');
  }
  return createCoverTemplate({
    generatedMediaId: ref.id,
    name,
    sourceKind: 'library',
    sourceResourceId: resourceId,
  });
}
