/**
 * NewAssetDialog — validation, the one request it makes, and the failure it
 * turns into an offer.
 *
 * The 409 case is the one worth reading closely. `uq_assets_scope_type_name`
 * makes a same-name-same-type create impossible, and the backend answers with
 * `existing_asset_id` precisely so the UI can say "that one already exists,
 * open it?". A dialog that rendered only the message would leave the user
 * retyping a name they cannot have — spec decision 14 in reverse.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const translate = (key: string, opts?: string | Record<string, unknown>): string => {
  const bundle: Record<string, string> = {
    'saveAsAsset.type.character': 'Character',
    'assets.dialog.title': 'New {{type}}',
    'assets.dialog.name': 'Name',
    'assets.dialog.role': 'Role',
    'assets.dialog.description': 'Description',
    'assets.dialog.create': 'Create',
    'assets.dialog.nameRequired': 'A Name Is Required',
    'assets.dialog.openExisting': 'Open Existing',
    // Distinct strings on purpose: a mock that always answered with the
    // generic default could not tell a MAPPED code from an unmapped one.
    'assets.err.asset_exists': 'An asset with this name and type already exists here.',
    'assets.err.generic': 'Something went wrong. Please try again.',
    'common.cancel': 'Cancel',
  };
  const fallback = typeof opts === 'string' ? opts : (opts?.defaultValue as string | undefined);
  let out = bundle[key] ?? fallback ?? key;
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

const createAsset = vi.fn();
vi.mock('../../../services/assetsService', async () => {
  const { GeneratedApiError } = await import('../../../services/apiEnvelope');
  return {
    createAsset: (...args: unknown[]) => createAsset(...args),
    GeneratedApiError,
  };
});

import { GeneratedApiError } from '../../../services/apiEnvelope';
import { NewAssetDialog } from './NewAssetDialog';

const onCreated = vi.fn();
const onClose = vi.fn();
const onOpenExisting = vi.fn();

/** The real `AssetSummary` shape — string id, `scope_id` present. */
const CREATED = {
  id: '727145299382534311',
  scope_id: '727145299382534200',
  asset_type: 'character' as const,
  name: 'Sang Yao',
  role_tag: 'lead',
  readiness: { state: 'draft' as const, missing: ['sheet'] },
  cover_file_id: null,
  is_system_preset: false,
};

function renderDialog() {
  return render(
    <NewAssetDialog
      open
      scopeId="727145299382534200"
      assetType="character"
      onClose={onClose}
      onCreated={onCreated}
      onOpenExisting={onOpenExisting}
    />,
  );
}

const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

const submit = () => fireEvent.submit(screen.getByTestId('new-asset-form'));

beforeEach(() => {
  createAsset.mockReset();
  onCreated.mockReset();
  onClose.mockReset();
  onOpenExisting.mockReset();
});

describe('NewAssetDialog — validation', () => {
  it('refuses an empty name and says so, without sending a request', () => {
    renderDialog();
    submit();
    expect(screen.getByTestId('new-asset-error').textContent).toContain('A Name Is Required');
    expect(createAsset).not.toHaveBeenCalled();
  });

  it('treats a whitespace-only name as empty', () => {
    // `min_length=1` would ACCEPT "   " server-side, so this is the only
    // thing standing between the user and an asset named three spaces.
    renderDialog();
    type('Name', '   ');
    submit();
    expect(createAsset).not.toHaveBeenCalled();
    expect(screen.getByTestId('new-asset-error')).toBeTruthy();
  });

  it('caps the fields at the lengths AssetCreate accepts', () => {
    renderDialog();
    expect(screen.getByLabelText('Name').getAttribute('maxlength')).toBe('200');
    expect(screen.getByLabelText('Role').getAttribute('maxlength')).toBe('40');
  });
});

describe('NewAssetDialog — create', () => {
  it('sends one trimmed request and hands the created asset back', async () => {
    createAsset.mockResolvedValue(CREATED);
    renderDialog();
    type('Name', '  Sang Yao  ');
    type('Role', ' lead ');
    type('Description', ' Late twenties. ');
    submit();

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(CREATED));
    expect(createAsset).toHaveBeenCalledTimes(1);
    expect(createAsset).toHaveBeenCalledWith('727145299382534200', {
      asset_type: 'character',
      name: 'Sang Yao',
      role_tag: 'lead',
      description: 'Late twenties.',
    });
  });
});

describe('NewAssetDialog — failures', () => {
  it('turns a name collision into an offer to open the existing asset', async () => {
    createAsset.mockRejectedValue(
      new GeneratedApiError(409, 'asset_exists', '…', {
        existing_asset_id: '727145299382534300',
      }),
    );
    renderDialog();
    type('Name', 'Sang Yao');
    submit();

    await waitFor(() =>
      expect(screen.getByTestId('new-asset-error').textContent).toContain('already exists'),
    );
    fireEvent.click(screen.getByText('Open Existing'));
    expect(onOpenExisting).toHaveBeenCalledWith('727145299382534300');
    expect(onCreated).not.toHaveBeenCalled();
  });

  it('reports an unmapped refusal without claiming the asset was created', async () => {
    createAsset.mockRejectedValue(new GeneratedApiError(503, 'http_503', 'upstream down'));
    renderDialog();
    type('Name', 'Sang Yao');
    submit();

    await waitFor(() =>
      expect(screen.getByTestId('new-asset-error').textContent).toContain('Something went wrong'),
    );
    // No id to open — a 503 is not a collision.
    expect(screen.queryByText('Open Existing')).toBeNull();
    expect(onCreated).not.toHaveBeenCalled();
  });

  it('stays usable after a failure', async () => {
    // The submit button re-enables in `finally`; a dialog stuck on "creating"
    // after one bad request is unrecoverable without closing it.
    createAsset.mockRejectedValueOnce(new GeneratedApiError(503, 'http_503', 'down'));
    createAsset.mockResolvedValueOnce(CREATED);
    renderDialog();
    type('Name', 'Sang Yao');
    submit();
    await waitFor(() => expect(screen.getByTestId('new-asset-error')).toBeTruthy());

    submit();
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(CREATED));
    // …and the stale error is gone rather than sitting under a success.
    expect(screen.queryByTestId('new-asset-error')).toBeNull();
  });
});
