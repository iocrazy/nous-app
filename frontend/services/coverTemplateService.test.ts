/**
 * Unit tests for coverTemplateService — the 样图模板库 client (migration 435).
 *
 * ⚠️ Fixtures use the REAL wire shape: every snowflake id is a JSON **string**,
 * because that is what `cover_templates_router.py` emits (it stringifies
 * explicitly, the `canvases` convention). Prettifying these into numbers is
 * exactly the class of drift that produced the 2026-08-12 storyboard incident.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockApiFetch = vi.fn();
vi.mock('./apiClient', () => ({
  apiFetch: (...args: unknown[]) => mockApiFetch(...args),
}));

const mockImportFile = vi.fn();
const mockImportResource = vi.fn();
vi.mock('../features/canvas-core/smart/mediaImport', () => ({
  importCanvasMedia: (...args: unknown[]) => mockImportFile(...args),
  importResourceAsCanvasMedia: (...args: unknown[]) => mockImportResource(...args),
}));

import {
  CoverTemplateError,
  addCoverTemplateFromFile,
  addCoverTemplateFromResource,
  createCoverTemplate,
  deleteCoverTemplate,
  listCoverTemplates,
  markCoverTemplatesUsed,
} from './coverTemplateService';

const TEMPLATE = {
  id: '341588599799820',
  name: 'Bold headline',
  generated_media_id: '341582104263581',
  image_url: '/api/v1/generated-media/341582104263581/cover',
  source_kind: 'upload' as const,
  source_resource_id: null,
  usage_count: 12,
  last_used_at: null,
  created_at: '2026-08-22T00:00:00Z',
};

function respond(data: unknown) {
  mockApiFetch.mockResolvedValueOnce({ json: async () => ({ data }) });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('listCoverTemplates', () => {
  it('unwraps {data:{items}} and returns the rows', async () => {
    respond({ items: [TEMPLATE] });
    const rows = await listCoverTemplates();

    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/cover-templates', undefined);
    expect(rows).toHaveLength(1);
    expect(rows[0].name).toBe('Bold headline');
  });

  it('returns [] when the server sends an empty list', async () => {
    respond({ items: [] });
    await expect(listCoverTemplates()).resolves.toEqual([]);
  });

  it('keeps every snowflake id a string', async () => {
    // Not cosmetic: these exceed 2^53. A number here loses the low digits and
    // the template silently addresses a different row.
    respond({ items: [TEMPLATE] });
    const [row] = await listCoverTemplates();

    expect(typeof row.id).toBe('string');
    expect(typeof row.generated_media_id).toBe('string');
  });
});

describe('createCoverTemplate', () => {
  it('POSTs snake_case body and returns the row', async () => {
    respond(TEMPLATE);
    const row = await createCoverTemplate({
      generatedMediaId: '341582104263581',
      name: 'Bold headline',
      sourceKind: 'library',
      sourceResourceId: '99',
    });

    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/cover-templates', {
      method: 'POST',
      json: {
        generated_media_id: '341582104263581',
        name: 'Bold headline',
        source_kind: 'library',
        source_resource_id: '99',
      },
    });
    expect(row.id).toBe('341588599799820');
  });

  it('maps a 404 to a typed not-found failure, not a bare Error', async () => {
    mockApiFetch.mockRejectedValueOnce(Object.assign(new Error('nope'), { status: 404 }));

    await expect(
      createCoverTemplate({ generatedMediaId: '1', name: 'x' }),
    ).rejects.toMatchObject({ failure: 'not-found', status: 404 });
  });

  it('maps a transport failure (no status) to network', async () => {
    mockApiFetch.mockRejectedValueOnce(new Error('offline'));

    await expect(
      createCoverTemplate({ generatedMediaId: '1', name: 'x' }),
    ).rejects.toMatchObject({ failure: 'network' });
  });
});

describe('addCoverTemplateFromFile', () => {
  it('imports first, then saves the returned generated-media id', async () => {
    // The ORDER is the point: the template must cite a durable
    // /generated-media/ id, because that is the only URL shape the image
    // generation bridge will read as a reference.
    mockImportFile.mockResolvedValueOnce({
      url: '/api/v1/generated-media/341582104263581/cover',
      kind: 'image',
      id: '341582104263581',
    });
    respond(TEMPLATE);

    const file = new File(['x'], 'ref.png', { type: 'image/png' });
    const row = await addCoverTemplateFromFile(file, 'Bold headline');

    expect(mockImportFile).toHaveBeenCalledWith(file, null, null);
    expect(mockApiFetch).toHaveBeenCalledWith(
      '/api/v1/cover-templates',
      expect.objectContaining({
        json: expect.objectContaining({
          generated_media_id: '341582104263581',
          source_kind: 'upload',
        }),
      }),
    );
    expect(row.image_url).toBe('/api/v1/generated-media/341582104263581/cover');
  });

  it('refuses a video and never reaches the template endpoint', async () => {
    mockImportFile.mockResolvedValueOnce({ url: '/u', kind: 'video', id: '5' });

    const file = new File(['x'], 'clip.mp4', { type: 'video/mp4' });
    await expect(addCoverTemplateFromFile(file, 'x')).rejects.toBeInstanceOf(
      CoverTemplateError,
    );
    expect(mockApiFetch).not.toHaveBeenCalled();
  });

  it('fails loudly when the import returns no id', async () => {
    // A missing id means the backend contract moved. Saving anyway would
    // create a template pointing nowhere — a broken thumbnail the user
    // cannot explain and we cannot trace.
    mockImportFile.mockResolvedValueOnce({ url: '/u', kind: 'image' });

    const file = new File(['x'], 'ref.png', { type: 'image/png' });
    await expect(addCoverTemplateFromFile(file, 'x')).rejects.toMatchObject({
      failure: 'server',
    });
    expect(mockApiFetch).not.toHaveBeenCalled();
  });
});

describe('addCoverTemplateFromResource', () => {
  it('records the originating resource id as provenance', async () => {
    mockImportResource.mockResolvedValueOnce({
      url: '/api/v1/generated-media/341582104263581/cover',
      kind: 'image',
      id: '341582104263581',
    });
    respond({ ...TEMPLATE, source_kind: 'library', source_resource_id: '777' });

    const row = await addCoverTemplateFromResource('777', 'From library');

    expect(mockImportResource).toHaveBeenCalledWith('777');
    expect(mockApiFetch).toHaveBeenCalledWith(
      '/api/v1/cover-templates',
      expect.objectContaining({
        json: expect.objectContaining({
          source_kind: 'library',
          source_resource_id: '777',
        }),
      }),
    );
    expect(row.source_resource_id).toBe('777');
  });
});

describe('deleteCoverTemplate', () => {
  it('DELETEs and reports whether a row went away', async () => {
    respond({ deleted: true });
    await expect(deleteCoverTemplate('341588599799820')).resolves.toBe(true);
    expect(mockApiFetch).toHaveBeenCalledWith(
      '/api/v1/cover-templates/341588599799820',
      { method: 'DELETE' },
    );
  });
});

describe('markCoverTemplatesUsed', () => {
  it('does not call the server for an empty list', async () => {
    await markCoverTemplatesUsed([]);
    expect(mockApiFetch).not.toHaveBeenCalled();
  });

  it('swallows failures so a cosmetic counter can never break a generation', async () => {
    // The one deliberate swallow in this module. It is logged, and the reason
    // is that this call happens AFTER the generation the user asked for has
    // already been dispatched successfully.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mockApiFetch.mockRejectedValueOnce(Object.assign(new Error('boom'), { status: 500 }));

    await expect(markCoverTemplatesUsed(['1', '2'])).resolves.toBeUndefined();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });
});
