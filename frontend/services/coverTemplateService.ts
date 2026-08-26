// frontend/services/coverTemplateService.ts
//
// Cover Studio's template library (migration 441). A template is a picture you
// liked, kept as an example and handed to the model as a reference image. It
// does not replace the style, which is text and lives in `skills`.
//
// ★ The library IS a folder in the resource library — a system folder flagged
// `system_key='cover_templates'` (the user's existing “封面” folder is adopted
// when there is one). There is no separate template table any more: a
// template is simply an image resource inside that folder, and its id is the
// resource id. Adding a template means putting a picture into the folder;
// removing one means removing it there. The backend guards the folder itself
// against rename / move / trash (typed 409 `system_folder`).
//
// What still needs a detour is the MODEL side: the image generation bridge
// only accepts `/api/v1/generated-media/{id}/(cover|stream|file)` URLs as
// references and silently drops anything else (`generated_media_local_path()`).
// So a template is imported into generated-media at the moment it is put into
// the reference pool — `resolveCoverTemplateReference` — never stored that way.

import { apiFetch } from './apiClient';
import { promoteGeneration } from './generatedMediaService';
import { linkExistingResource, uploadResource } from './resourceService';
import { importResourceAsCanvasMedia } from '../features/canvas-core/smart/mediaImport';
import type { Resource } from '../types';

/** The folder that backs the library. `adopted` = it was the user's own
 *  “封面” folder rather than one we created. */
export interface CoverTemplateFolder {
  folder_id: string;
  name: string;
  adopted: boolean;
}

/** Wire shape of one template. `resource_id` is a STRING (snowflake > 2^53). */
export interface CoverTemplate {
  resource_id: string;
  name: string;
  mime_type: string | null;
  /** API-relative, ready for <img src> once prefixed with the API origin. */
  thumb_url: string;
  usage_count: number;
  last_used_at: string | null;
}

export interface CoverTemplateList {
  folder: CoverTemplateFolder;
  items: CoverTemplate[];
}

/** A template made usable as a generation reference. */
export interface CoverTemplateReference {
  /** generated_media row id, as a STRING. */
  genId: string;
  /** Valid for both <img src> and params.source_urls. */
  url: string;
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

function wrap(err: unknown, fallback: string): CoverTemplateError {
  if (err instanceof CoverTemplateError) return err;
  const status = (err as { status?: number })?.status;
  if (typeof status === 'number') {
    return new CoverTemplateError(
      failureFromStatus(status),
      (err as Error).message || fallback,
      status,
    );
  }
  return new CoverTemplateError('network', (err as Error)?.message || fallback);
}

async function call<T>(path: string, init?: Parameters<typeof apiFetch>[1]): Promise<T> {
  let response: Response;
  try {
    response = await apiFetch(path, init);
  } catch (err) {
    // apiFetch throws ApiError for non-2xx and a plain error for transport
    // failures. Both land here; `status` tells them apart.
    throw wrap(err, 'cover template request failed');
  }
  const json = (await response.json()) as { data?: T };
  if (json?.data === undefined) {
    throw new CoverTemplateError('server', 'response carried no data');
  }
  return json.data;
}

export async function getCoverTemplateFolder(): Promise<CoverTemplateFolder> {
  return call<CoverTemplateFolder>('/api/v1/cover-templates/folder');
}

export async function listCoverTemplates(): Promise<CoverTemplateList> {
  const data = await call<CoverTemplateList>('/api/v1/cover-templates');
  return { folder: data.folder, items: data.items ?? [] };
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
export async function markCoverTemplatesUsed(resourceIds: string[]): Promise<void> {
  if (resourceIds.length === 0) return;
  try {
    await call<{ counted: number }>('/api/v1/cover-templates/use', {
      method: 'POST',
      json: { resource_ids: resourceIds },
    });
  } catch (err) {
    console.error('[coverTemplateService] markCoverTemplatesUsed failed:', err);
  }
}

/**
 * Make a template usable as a generation reference.
 *
 * Called when the tile goes INTO the pool, not when the template is saved:
 * the generated-media row is a durable copy minted for the model, and minting
 * one per saved picture that may never be used is waste the user pays for in
 * storage.
 */
export async function resolveCoverTemplateReference(
  resourceId: string,
): Promise<CoverTemplateReference> {
  let ref: Awaited<ReturnType<typeof importResourceAsCanvasMedia>>;
  try {
    ref = await importResourceAsCanvasMedia(resourceId);
  } catch (err) {
    throw wrap(err, 'could not import the template as a reference');
  }
  if (ref.kind !== 'image') {
    throw new CoverTemplateError('not-an-image', 'a cover template must be an image');
  }
  if (!ref.id) {
    // The backend does return it; a missing id means the contract moved.
    // Failing loudly beats a reference that points nowhere.
    throw new CoverTemplateError('server', 'import returned no generated-media id');
  }
  return { genId: ref.id, url: ref.url };
}

/** The template a freshly added resource becomes — same shape the list gives. */
export function templateFromResource(resource: Resource): CoverTemplate {
  return {
    resource_id: String(resource.id),
    name: resource.filename,
    mime_type: resource.mime_type ?? null,
    thumb_url: `/api/v1/resources/${resource.id}/cover`,
    usage_count: 0,
    last_used_at: null,
  };
}

/** Upload a local picture straight into the template folder. */
export async function addCoverTemplateFromFile(
  file: File,
  scopeId: string,
): Promise<CoverTemplate> {
  if (!(file.type || '').toLowerCase().startsWith('image/')) {
    throw new CoverTemplateError('not-an-image', 'a cover template must be an image');
  }
  const folder = await getCoverTemplateFolder();
  let resource: Resource;
  try {
    resource = await uploadResource(file, scopeId, folder.folder_id);
  } catch (err) {
    throw wrap(err, 'upload into the template folder failed');
  }
  return templateFromResource(resource);
}

/**
 * Put a picture already in the library into the template folder.
 *
 * `link-existing` adds a folder membership; the picture stays wherever else
 * it already lives. So "add as template" never moves or duplicates the file.
 */
export async function addCoverTemplateFromResource(
  resourceId: string,
  scopeId: string,
): Promise<CoverTemplate> {
  const folder = await getCoverTemplateFolder();
  let resource: Resource;
  try {
    resource = await linkExistingResource(resourceId, scopeId, folder.folder_id);
  } catch (err) {
    throw wrap(err, 'linking into the template folder failed');
  }
  return templateFromResource(resource);
}

/**
 * Keep a generated cover as a template: promote it into the library (the
 * resource id is returned so the caller can reuse it for "Use this cover"
 * without promoting twice), then put that resource into the folder.
 */
export async function saveGeneratedCoverAsTemplate(
  genId: string,
  scopeId: string,
  alreadyPromotedResourceId?: string,
): Promise<{ template: CoverTemplate; resourceId: string }> {
  let resourceId = alreadyPromotedResourceId;
  if (!resourceId) {
    try {
      resourceId = (await promoteGeneration(genId)).promoted_resource_id;
    } catch (err) {
      throw wrap(err, 'promoting the cover failed');
    }
  }
  const template = await addCoverTemplateFromResource(resourceId, scopeId);
  return { template, resourceId };
}
