/**
 * SaveAsAssetDialog — the My Uploads (resource) variant, P6 ruling E.
 *
 * The generation variant has its own suite next door; this one covers only
 * what the second subject changes: where the prefill and the cover come from,
 * which endpoint the confirm hits, that cancelling sends NOTHING, and that
 * every typed refusal the new route can raise reaches the user as its own
 * sentence rather than "something went wrong".
 *
 * The error bundle below is loaded from `public/locales/en.json` rather than
 * hand-written: a test with its own copy of the strings would stay green while
 * the shipped locale had no key at all, which is precisely the failure this
 * file is here to prevent.
 */

import fs from 'node:fs';
import path from 'node:path';

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const EN = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../../public/locales/en.json'), 'utf8'),
) as Record<string, any>;

/** The real shipped strings, flattened to the keys the dialog resolves. */
const BUNDLE: Record<string, string> = {
  ...Object.fromEntries(
    Object.entries(EN.saveAsAsset.err as Record<string, string>).map(([k, v]) => [
      `saveAsAsset.err.${k}`,
      v,
    ]),
  ),
  'resources.saveAsAssetSource': EN.resources.saveAsAssetSource,
  'saveAsAsset.coverUnavailable': EN.saveAsAsset.coverUnavailable,
};

const translate = (
  key: string,
  opts?: string | Record<string, unknown>,
): string => {
  const fallback = typeof opts === 'string' ? opts : (opts?.defaultValue as string | undefined);
  let out = BUNDLE[key] ?? fallback ?? key;
  if (opts && typeof opts === 'object') {
    for (const [name, value] of Object.entries(opts)) {
      if (name === 'defaultValue') continue;
      out = out.split(`{{${name}}}`).join(String(value));
    }
  }
  return out;
};

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: string | Record<string, unknown>) => translate(k, o),
  }),
}));

const addToast = vi.fn();
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));

vi.mock('../../services/generatedMediaService', () => ({
  generatedMediaCoverUrl: (id: string) => `https://api.test/gen/${id}/cover`,
  generatedMediaStreamUrl: (id: string) => `https://api.test/gen/${id}/stream`,
}));

// The whole module is stubbed: `resourceService` reaches for the Supabase
// client at import time, and this dialog needs exactly one function from it.
vi.mock('../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `https://api.test/resources/${id}/cover`,
}));

const searchAssets = vi.fn();
const createAsset = vi.fn();
const fetchAsset = vi.fn();
vi.mock('../../services/assetsService', async () => {
  const { GeneratedApiError } = await import('../../services/apiEnvelope');
  return {
    searchAssets: (...a: unknown[]) => searchAssets(...a),
    createAsset: (...a: unknown[]) => createAsset(...a),
    fetchAsset: (...a: unknown[]) => fetchAsset(...a),
    GeneratedApiError,
  };
});

const saveGenerationAsAsset = vi.fn();
const batchGenerated = vi.fn();
const saveResourceAsAsset = vi.fn();
vi.mock('../../services/generatedService', async () => {
  const { GeneratedApiError } = await import('../../services/apiEnvelope');
  return {
    saveGenerationAsAsset: (...a: unknown[]) => saveGenerationAsAsset(...a),
    batchGenerated: (...a: unknown[]) => batchGenerated(...a),
    saveResourceAsAsset: (...a: unknown[]) => saveResourceAsAsset(...a),
    GeneratedApiError,
  };
});

import { assetNameFromFilename, SaveAsAssetDialog } from './SaveAsAssetDialog';
import type { SaveAsAssetResource } from './SaveAsAssetDialog';
import { GeneratedApiError } from '../../services/apiEnvelope';
import type { AssetDetail, AssetSummary } from '../../services/assetsService';

// ─── Fixtures: real wire shapes, string ids ─────────────────────────────────

const SCOPE = '727145299382534200';

/** A `resources` row as `GET /api/v1/resources` returns it, trimmed to the
 *  columns this dialog reads. Ids are strings — Snowflakes never ride as
 *  numbers through this client. */
const IMAGE_RESOURCE: SaveAsAssetResource = {
  id: '742318905233409001',
  filename: 'sang-yao-turnaround.png',
  mime_type: 'image/png',
  file_type: 'image',
  created_at: '2026-09-01T04:15:00Z',
};

const summary = (over: Partial<AssetSummary> = {}): AssetSummary => ({
  id: '727145299382534202',
  scope_id: SCOPE,
  asset_type: 'character',
  name: 'Yi Heng',
  role_tag: 'lead',
  readiness: { state: 'ready', missing: [] },
  cover_file_id: null,
  is_system_preset: false,
  ...over,
});

const YI_HENG: AssetDetail = {
  ...summary(),
  loadouts: [{ id: '727145299382534511', name: 'Default', is_default: true }],
};

/** The route's own 201, including the key the generation route does not have. */
const OK_RESULT = {
  generation: { id: '727145299382534177', review_state: 'in_assets' },
  asset_id: '727145299382534202',
  resource_id: IMAGE_RESOURCE.id,
  generated_id: '727145299382534177',
};

const renderDialog = (resource: SaveAsAssetResource = IMAGE_RESOURCE) => {
  const onClose = vi.fn();
  const onDone = vi.fn();
  render(
    <SaveAsAssetDialog
      open
      scopeId={SCOPE}
      resource={resource}
      onClose={onClose}
      onDone={onDone}
    />,
  );
  return { onClose, onDone };
};

/** Like {@link renderDialog}, but keeps the handle needed to re-render the
 *  SAME mounted dialog on a different resource — the component stays mounted
 *  between openings, which is what makes the reset behaviour testable. */
const renderDialogRaw = (resource: SaveAsAssetResource) => {
  const view = render(
    <SaveAsAssetDialog
      open
      scopeId={SCOPE}
      resource={resource}
      onClose={vi.fn()}
      onDone={vi.fn()}
    />,
  );
  return {
    rerender: (next: SaveAsAssetResource) =>
      view.rerender(
        <SaveAsAssetDialog
          open
          scopeId={SCOPE}
          resource={next}
          onClose={vi.fn()}
          onDone={vi.fn()}
        />,
      ),
  };
};

const primary = () => screen.getByTestId('sa-primary') as HTMLButtonElement;

beforeEach(() => {
  addToast.mockReset();
  searchAssets.mockReset().mockResolvedValue([summary()]);
  createAsset.mockReset();
  fetchAsset.mockReset().mockResolvedValue(YI_HENG);
  saveGenerationAsAsset.mockReset();
  batchGenerated.mockReset();
  saveResourceAsAsset.mockReset().mockResolvedValue(OK_RESULT);
});

// ─── Prefill ────────────────────────────────────────────────────────────────

describe('assetNameFromFilename', () => {
  it.each([
    ['sang-yao-turnaround.png', 'sang-yao-turnaround'],
    ['a.b.c.wav', 'a.b.c'],
    // No extension, a dotfile, and a trailing dot: all left exactly as they
    // are. Slicing at the last dot regardless would produce '' for '.env'.
    ['README', 'README'],
    ['.env', '.env'],
    ['trailing.', 'trailing.'],
  ])('%s → %s', (input, expected) => {
    expect(assetNameFromFilename(input)).toBe(expected);
  });
});

describe('SaveAsAssetDialog — resource variant prefill', () => {
  it('shows the resource cover and filename, sourced as My Uploads', async () => {
    renderDialog();
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());

    const cover = screen.getByAltText(IMAGE_RESOURCE.filename) as HTMLImageElement;
    // The RESOURCE cover endpoint, not a generated-media one: this file may
    // have no `generated_media` row until the server mints one on confirm.
    expect(cover.getAttribute('src')).toBe(
      `https://api.test/resources/${IMAGE_RESOURCE.id}/cover`,
    );
    expect(screen.getByText(EN.resources.saveAsAssetSource)).toBeTruthy();
    // No batch strip: there is no batch form of this entry.
    expect(screen.queryByTestId('sa-more-strip')).toBeNull();
  });

  it('falls back to a kind icon when the cover 404s, instead of a broken image', async () => {
    renderDialog();
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());

    const cover = screen.getByAltText(IMAGE_RESOURCE.filename);
    expect(screen.queryByTestId('sa-cover-fallback')).toBeNull();

    // `/resources/{id}/cover` answers 404 for an upload with no thumbnail —
    // most often audio, which this menu is what first sends here. The browser
    // reports that as an error event on the <img>, and with no handler it
    // paints the broken-image glyph inside the bordered square: the user reads
    // "this file is damaged" for a file that is perfectly fine.
    fireEvent.error(cover);

    const fallback = await screen.findByTestId('sa-cover-fallback');
    expect(fallback.textContent).toContain(EN.saveAsAsset.coverUnavailable);
    // The <img> is gone, not merely covered — a hidden one keeps retrying.
    expect(screen.queryByAltText(IMAGE_RESOURCE.filename)).toBeNull();
    // Still labelled with the filename, so the placeholder is not a hole to a
    // screen reader.
    expect(fallback.getAttribute('aria-label')).toBe(IMAGE_RESOURCE.filename);
  });

  it('picks the audio icon for an audio upload and the image icon otherwise', async () => {
    renderDialog({
      ...IMAGE_RESOURCE,
      filename: 'theme.mp3',
      mime_type: 'audio/mpeg',
      file_type: 'audio',
    });
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());
    fireEvent.error(screen.getByAltText('theme.mp3'));

    const fallback = await screen.findByTestId('sa-cover-fallback');
    // lucide renders its name onto the svg, which is the only handle a test
    // has on WHICH icon was chosen. An audio file drawn as a picture frame is
    // a small lie, and the icon is the whole content of this placeholder.
    //
    // `audio-lines`, not this file's former `file-audio`: the icon now comes
    // from `mediaKindPlaceholder`, so the SAME file gets the SAME icon here
    // and on the inbox card the server is about to mint for it.
    expect(fallback.querySelector('svg')?.getAttribute('class')).toContain('audio-lines');
  });

  it('a fresh cover is trusted again after the dialog reopens on another file', async () => {
    const { rerender } = renderDialogRaw(IMAGE_RESOURCE);
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());
    fireEvent.error(screen.getByAltText(IMAGE_RESOURCE.filename));
    await screen.findByTestId('sa-cover-fallback');

    // The previous file's 404 says nothing about this one; a sticky flag would
    // hide every cover for the rest of the session.
    rerender({ ...IMAGE_RESOURCE, id: '742318905233409009', filename: 'other.png' });
    await waitFor(() => expect(screen.queryByTestId('sa-cover-fallback')).toBeNull());
    expect(screen.getByAltText('other.png')).toBeTruthy();
  });

  it('prefills the new-asset name from the filename WITHOUT its extension', async () => {
    renderDialog();
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('sa-create-button'));
    const input = (await screen.findByTestId('sa-new-name')) as HTMLInputElement;
    expect(input.value).toBe('sang-yao-turnaround');
  });

  it('asks for no suggestion — a resource has no source asset', async () => {
    renderDialog();
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());
    expect(fetchAsset).not.toHaveBeenCalled();
  });
});

// ─── Confirm / cancel ───────────────────────────────────────────────────────

describe('SaveAsAssetDialog — resource variant submit', () => {
  it('confirms against the resource endpoint with string ids', async () => {
    const { onClose, onDone } = renderDialog();
    await waitFor(() => expect(screen.getByText('Yi Heng')).toBeTruthy());

    fireEvent.click(screen.getByText('Yi Heng'));
    await waitFor(() => expect(primary().disabled).toBe(false));
    fireEvent.click(primary());

    await waitFor(() => expect(saveResourceAsAsset).toHaveBeenCalledTimes(1));
    // `loadout_id` rides along because Yi Heng is a character whose default
    // loadout resolved — the same rule as the generation path, unchanged.
    expect(saveResourceAsAsset).toHaveBeenCalledWith(SCOPE, IMAGE_RESOURCE.id, {
      asset_id: '727145299382534202',
      slot: 'unsorted',
      loadout_id: '727145299382534511',
    });
    // Types, not just values: a number here is the Snowflake-precision bug.
    const [scopeArg, idArg] = saveResourceAsAsset.mock.calls[0];
    expect(typeof scopeArg).toBe('string');
    expect(typeof idArg).toBe('string');

    // The generation endpoints are NOT a fallback path for this subject.
    expect(saveGenerationAsAsset).not.toHaveBeenCalled();
    expect(batchGenerated).not.toHaveBeenCalled();

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(onDone.mock.calls[0][0]).toEqual({
      attachedCount: 1,
      failed: [],
      assetId: '727145299382534202',
      assetName: 'Yi Heng',
      slot: 'unsorted',
    });
    expect(onClose).toHaveBeenCalled();
    expect(addToast).toHaveBeenCalledWith(expect.stringContaining('Yi Heng'), 'success');
  });

  it('creates the asset first, then attaches the resource to it', async () => {
    createAsset.mockResolvedValue({ ...summary({ id: '742318905233410001', name: 'Sang Yao' }) });
    renderDialog();
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('sa-create-button'));
    await waitFor(() => expect(primary().disabled).toBe(false));
    fireEvent.click(primary());

    await waitFor(() => expect(saveResourceAsAsset).toHaveBeenCalledTimes(1));
    expect(createAsset).toHaveBeenCalledWith(SCOPE, {
      asset_type: 'character',
      name: 'sang-yao-turnaround',
      source: 'generated',
    });
    expect(saveResourceAsAsset.mock.calls[0][2]).toEqual({
      asset_id: '742318905233410001',
      slot: 'unsorted',
    });
  });

  it('cancelling makes NO request — no inbox row is minted for a dialog nobody confirmed', async () => {
    const { onClose } = renderDialog();
    await waitFor(() => expect(screen.getByText('Yi Heng')).toBeTruthy());

    // Pick a target first: cancelling with nothing chosen would prove less.
    fireEvent.click(screen.getByText('Yi Heng'));
    await waitFor(() => expect(primary().disabled).toBe(false));
    fireEvent.click(screen.getByText('Cancel'));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(saveResourceAsAsset).not.toHaveBeenCalled();
    expect(createAsset).not.toHaveBeenCalled();
  });
});

// ─── Typed refusals ─────────────────────────────────────────────────────────

describe('SaveAsAssetDialog — resource variant refusals', () => {
  const CODES: [string, number][] = [
    ['resource_not_accessible', 404],
    ['resource_not_found', 404],
    ['resource_kind_unsupported', 422],
    ['resource_file_unresolved', 422],
    ['not_a_member', 403],
  ];

  it.each(CODES)('%s renders its own sentence, not the generic one', async (code, status) => {
    saveResourceAsAsset.mockRejectedValue(
      new GeneratedApiError(status, code, 'server detail'),
    );
    const { onClose, onDone } = renderDialog();
    await waitFor(() => expect(screen.getByText('Yi Heng')).toBeTruthy());

    fireEvent.click(screen.getByText('Yi Heng'));
    await waitFor(() => expect(primary().disabled).toBe(false));
    fireEvent.click(primary());

    await waitFor(() => expect(addToast).toHaveBeenCalled());
    const [message, kind] = addToast.mock.calls[0];
    expect(kind).toBe('error');
    expect(message).toBe(EN.saveAsAsset.err[code]);
    expect(message).not.toBe(EN.saveAsAsset.err.generic);

    // A refusal is not a done deal: the dialog stays open on the answers the
    // user already gave, so a fixable reason can actually be fixed.
    expect(onDone).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('gives the cross-team refusal a different sentence from "not available here"', () => {
    // Same HTTP status, opposite advice: `resource_not_accessible` means the
    // file is not yours, `resource_not_found` means it IS yours but is not
    // filed in this workspace. One sentence for both would tell the owner
    // their own file does not exist.
    expect(EN.saveAsAsset.err.resource_not_found).not.toBe(
      EN.saveAsAsset.err.resource_not_accessible,
    );
  });

  it('an unmapped code still reaches the user as the generic sentence', async () => {
    saveResourceAsAsset.mockRejectedValue(
      new GeneratedApiError(500, 'some_new_backend_code', 'boom'),
    );
    renderDialog();
    await waitFor(() => expect(screen.getByText('Yi Heng')).toBeTruthy());
    fireEvent.click(screen.getByText('Yi Heng'));
    await waitFor(() => expect(primary().disabled).toBe(false));
    fireEvent.click(primary());

    await waitFor(() => expect(addToast).toHaveBeenCalled());
    expect(addToast.mock.calls[0][0]).toBe(EN.saveAsAsset.err.generic);
  });
});
