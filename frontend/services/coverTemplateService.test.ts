/**
 * Unit tests for coverTemplateService — the template-library client
 * (migration 441: the library is a system folder in the resource library).
 *
 * ⚠️ Fixtures use the REAL wire shape: every snowflake id is a JSON **string**,
 * because that is what `cover_templates_router.py` emits. Prettifying these
 * into numbers is exactly the class of drift that produced the 2026-08-12
 * storyboard incident.
 *
 * What is pinned:
 *   1. Adding goes INTO THE FOLDER — upload lands there, a library pick is
 *      linked there — never through generated-media.
 *   2. A template becomes a model reference only when resolved, via the
 *      generated-media import (the bridge accepts nothing else).
 *   3. Usage ticks send resource ids and NEVER throw.
 *   4. Saving a generated cover reuses an already-promoted resource id
 *      instead of promoting the same picture twice.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockApiFetch = vi.fn();
vi.mock('./apiClient', () => ({
  apiFetch: (...args: unknown[]) => mockApiFetch(...args),
}));

const mockImportResource = vi.fn();
vi.mock('../features/canvas-core/smart/mediaImport', () => ({
  importResourceAsCanvasMedia: (...args: unknown[]) => mockImportResource(...args),
}));

const mockUpload = vi.fn();
const mockLink = vi.fn();
vi.mock('./resourceService', () => ({
  uploadResource: (...args: unknown[]) => mockUpload(...args),
  linkExistingResource: (...args: unknown[]) => mockLink(...args),
}));

const mockPromote = vi.fn();
vi.mock('./generatedMediaService', () => ({
  promoteGeneration: (...args: unknown[]) => mockPromote(...args),
}));

import {
  CoverTemplateError,
  addCoverTemplateFromFile,
  addCoverTemplateFromResource,
  getCoverTemplateFolder,
  listCoverTemplates,
  markCoverTemplatesUsed,
  resolveCoverTemplateReference,
  saveGeneratedCoverAsTemplate,
} from './coverTemplateService';

const FOLDER = { folder_id: '341588599799820', name: '封面', adopted: true };
const TEMPLATE = {
  resource_id: '341582104263581',
  name: 'bold-headline.png',
  mime_type: 'image/png',
  thumb_url: '/api/v1/resources/341582104263581/cover',
  usage_count: 12,
  last_used_at: null,
};
/** What uploadResource / linkExistingResource hand back (a Resource). */
const RESOURCE = { id: '341590000000001', filename: 'new-pick.png', mime_type: 'image/png' };

function respond(data: unknown) {
  mockApiFetch.mockResolvedValueOnce({ json: async () => ({ data }) });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('reading the library', () => {
  it('lists the folder and its pictures', async () => {
    respond({ folder: FOLDER, items: [TEMPLATE] });

    const list = await listCoverTemplates();

    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/cover-templates', undefined);
    expect(list.folder).toEqual(FOLDER);
    expect(list.items).toEqual([TEMPLATE]);
    expect(typeof list.items[0].resource_id).toBe('string');
  });

  it('returns the folder on its own', async () => {
    respond(FOLDER);
    expect(await getCoverTemplateFolder()).toEqual(FOLDER);
    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/cover-templates/folder', undefined);
  });

  it('turns HTTP failures into a typed error', async () => {
    mockApiFetch.mockRejectedValueOnce(Object.assign(new Error('nope'), { status: 403 }));
    await expect(listCoverTemplates()).rejects.toMatchObject({
      name: 'CoverTemplateError',
      failure: 'forbidden',
      status: 403,
    });
  });
});

describe('adding a template puts a picture INTO THE FOLDER', () => {
  it('upload: straight into the folder, and the result is the list shape', async () => {
    respond(FOLDER);
    mockUpload.mockResolvedValueOnce(RESOURCE);
    const file = new File(['x'], 'new-pick.png', { type: 'image/png' });

    const tpl = await addCoverTemplateFromFile(file, 'scope-1');

    expect(mockUpload).toHaveBeenCalledWith(file, 'scope-1', FOLDER.folder_id);
    expect(mockImportResource).not.toHaveBeenCalled();
    expect(tpl).toEqual({
      resource_id: RESOURCE.id,
      name: 'new-pick.png',
      mime_type: 'image/png',
      thumb_url: `/api/v1/resources/${RESOURCE.id}/cover`,
      usage_count: 0,
      last_used_at: null,
    });
  });

  it('upload: a non-image is refused before any request is made', async () => {
    const file = new File(['x'], 'clip.mp4', { type: 'video/mp4' });
    await expect(addCoverTemplateFromFile(file, 'scope-1')).rejects.toMatchObject({
      failure: 'not-an-image',
    });
    expect(mockApiFetch).not.toHaveBeenCalled();
    expect(mockUpload).not.toHaveBeenCalled();
  });

  it('library pick: LINKED into the folder, not copied and not imported', async () => {
    respond(FOLDER);
    mockLink.mockResolvedValueOnce(RESOURCE);

    const tpl = await addCoverTemplateFromResource('777', 'scope-1');

    expect(mockLink).toHaveBeenCalledWith('777', 'scope-1', FOLDER.folder_id);
    expect(mockUpload).not.toHaveBeenCalled();
    expect(mockImportResource).not.toHaveBeenCalled();
    expect(tpl.resource_id).toBe(RESOURCE.id);
  });

  it('a failed link surfaces as a typed error, not a bare one', async () => {
    respond(FOLDER);
    mockLink.mockRejectedValueOnce(new Error('Failed to link resource'));
    await expect(addCoverTemplateFromResource('777', 'scope-1')).rejects.toBeInstanceOf(
      CoverTemplateError,
    );
  });
});

describe('resolving a template into a model reference', () => {
  it('imports through generated-media and returns the id + url pair', async () => {
    mockImportResource.mockResolvedValueOnce({
      kind: 'image',
      id: '900',
      url: '/api/v1/generated-media/900/cover',
    });

    const ref = await resolveCoverTemplateReference(TEMPLATE.resource_id);

    expect(mockImportResource).toHaveBeenCalledWith(TEMPLATE.resource_id);
    expect(ref).toEqual({ genId: '900', url: '/api/v1/generated-media/900/cover' });
  });

  it('refuses a video and a missing id loudly', async () => {
    mockImportResource.mockResolvedValueOnce({ kind: 'video', id: '1', url: '/x' });
    await expect(resolveCoverTemplateReference('1')).rejects.toMatchObject({
      failure: 'not-an-image',
    });
    mockImportResource.mockResolvedValueOnce({ kind: 'image', url: '/x' });
    await expect(resolveCoverTemplateReference('1')).rejects.toMatchObject({
      failure: 'server',
    });
  });
});

describe('usage ticks', () => {
  it('sends resource ids', async () => {
    respond({ counted: 2 });
    await markCoverTemplatesUsed(['1', '2']);
    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/cover-templates/use', {
      method: 'POST',
      json: { resource_ids: ['1', '2'] },
    });
  });

  it('sends nothing for an empty list and never throws', async () => {
    await markCoverTemplatesUsed([]);
    expect(mockApiFetch).not.toHaveBeenCalled();
    mockApiFetch.mockRejectedValueOnce(Object.assign(new Error('boom'), { status: 500 }));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    await expect(markCoverTemplatesUsed(['1'])).resolves.toBeUndefined();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe('saving a generated cover as a template', () => {
  it('promotes once, then links the resource into the folder', async () => {
    mockPromote.mockResolvedValueOnce({ promoted_resource_id: '9000' });
    respond(FOLDER);
    mockLink.mockResolvedValueOnce({ ...RESOURCE, id: '9000' });

    const out = await saveGeneratedCoverAsTemplate('600', 'scope-1');

    expect(mockPromote).toHaveBeenCalledWith('600');
    expect(mockLink).toHaveBeenCalledWith('9000', 'scope-1', FOLDER.folder_id);
    expect(out.resourceId).toBe('9000');
  });

  it('reuses an already-promoted resource id instead of promoting twice', async () => {
    respond(FOLDER);
    mockLink.mockResolvedValueOnce({ ...RESOURCE, id: '9000' });

    await saveGeneratedCoverAsTemplate('600', 'scope-1', '9000');

    expect(mockPromote).not.toHaveBeenCalled();
    expect(mockLink).toHaveBeenCalledWith('9000', 'scope-1', FOLDER.folder_id);
  });
});
