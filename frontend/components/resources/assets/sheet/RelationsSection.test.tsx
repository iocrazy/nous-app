/**
 * Relation sections: what each direction offers, and what the picker calls.
 *
 * The direction assertion is the one that matters. An INCOMING section is a
 * view of a row that lives on the other asset - listing it is honest, offering
 * to delete it from here is not, because this page is not where that link
 * belongs. Both directions render the same-looking row, so nothing but a test
 * distinguishes them.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { i18nMock, SCOPE_ID, UiModalStub } from './sheetTestUtils';

vi.mock('react-i18next', () => i18nMock);
vi.mock('../../../ui/primitives', () => ({ UiModal: UiModalStub }));
vi.mock('../../../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `/api/v1/resources/${id}/cover`,
  getResourceFileUrl: (id: string) => `/api/v1/resources/${id}/file`,
}));

const listAssets = vi.fn();
vi.mock('../../../../services/assetsService', () => ({
  listAssets: (...args: unknown[]) => listAssets(...args),
}));

import { RelationsSection } from './RelationsSection';
import { relationSectionsFor } from './assetSheetModel';
import {
  AUDIO_DETAIL,
  CHARACTER_DETAIL,
  COSTUME_DETAIL,
  makeDetail,
  makeRow,
} from './assetSheetFixtures';

const COSTUME_ROW = makeRow({
  id: '727145299382534310',
  asset_type: 'costume',
  name: 'Night Robe',
  role_tag: '',
});

const OTHER_COSTUME = makeRow({
  id: '727145299382534311',
  asset_type: 'costume',
  name: 'Travel Cloak',
  role_tag: '',
});

const PRESET_COSTUME = makeRow({
  id: '727145299382534312',
  scope_id: null,
  asset_type: 'costume',
  name: 'Preset Cloak',
  is_system_preset: true,
});

function renderSection(
  overrides: Partial<React.ComponentProps<typeof RelationsSection>> = {},
) {
  const props = {
    scopeId: SCOPE_ID,
    detail: CHARACTER_DETAIL,
    spec: relationSectionsFor('character', null)[0],
    related: { [COSTUME_ROW.id]: COSTUME_ROW } as Record<string, typeof COSTUME_ROW | undefined>,
    readOnly: false,
    onOpenAsset: vi.fn(),
    onAdd: vi.fn(),
    onRemove: vi.fn(),
    onError: vi.fn(),
    ...overrides,
  };
  return { ...render(<RelationsSection {...props} />), props };
}

beforeEach(() => {
  listAssets.mockReset();
  listAssets.mockResolvedValue([COSTUME_ROW, OTHER_COSTUME, PRESET_COSTUME]);
});

describe('rendering', () => {
  it('names the linked asset rather than its Snowflake', () => {
    renderSection();
    expect(within(screen.getByTestId('relation-row')).getByText('Night Robe')).toBeTruthy();
  });

  it('falls back to the raw id when the row could not be resolved', () => {
    // An asset we could not name is still an asset that is linked; dropping
    // the row would hide a real relation.
    renderSection({ related: {} });
    expect(screen.getByTestId('relation-row')).toHaveTextContent('727145299382534310');
  });

  it('an incoming section lists rows but offers no Add and no Remove', () => {
    renderSection({
      detail: COSTUME_DETAIL,
      spec: relationSectionsFor('costume', null)[0],
      related: { [CHARACTER_DETAIL.id]: CHARACTER_DETAIL },
    });
    expect(screen.getByTestId('relation-section')).toHaveAttribute('data-direction', 'incoming');
    expect(screen.getByTestId('relation-row')).toHaveTextContent('Sang Yao');
    expect(screen.queryByTestId('relation-add')).toBeNull();
    expect(screen.queryByTestId('relation-remove')).toBeNull();
  });

  it('a preset sheet offers no Add and no Remove', () => {
    renderSection({ readOnly: true });
    expect(screen.queryByTestId('relation-add')).toBeNull();
    expect(screen.queryByTestId('relation-remove')).toBeNull();
  });

  it('an audio asset with no subtype says why it cannot link, and still lists', () => {
    const detail = makeDetail({ ...AUDIO_DETAIL, id: AUDIO_DETAIL.id, subtype: null });
    renderSection({
      detail,
      spec: relationSectionsFor('audio', null)[0],
      related: {},
    });
    expect(screen.queryByTestId('relation-add')).toBeNull();
    expect(screen.getByTestId('relation-add-blocked')).toHaveTextContent(
      'Set an audio subtype before linking this asset',
    );
    expect(screen.getByTestId('relation-row')).toBeTruthy();
  });
});

describe('mutations', () => {
  it('Remove passes the target id AND the relation', async () => {
    // `DELETE /assets/{id}/links/{to}/{relation}` addresses one pair; sending
    // the id alone would be a different route.
    const { props } = renderSection();
    fireEvent.click(screen.getByTestId('relation-remove'));
    expect(props.onRemove).toHaveBeenCalledWith('727145299382534310', 'wears');
  });

  it('Add searches only the type the relation accepts', async () => {
    renderSection();
    fireEvent.click(screen.getByTestId('relation-add'));
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    expect(listAssets).toHaveBeenCalledWith(SCOPE_ID, {
      type: 'costume',
      q: undefined,
      limit: 30,
    });
  });

  it('the picker hides presets and this asset, and marks the already-linked', async () => {
    const { props } = renderSection();
    fireEvent.click(screen.getByTestId('relation-add'));
    await waitFor(() => expect(screen.getAllByTestId('link-candidate').length).toBe(2));

    const ids = screen
      .getAllByTestId('link-candidate')
      .map((el) => el.getAttribute('data-asset-id'));
    // A preset has no scope, and a link's two ends must share one - so it can
    // never be a valid target and is not offered.
    expect(ids).toEqual(['727145299382534310', '727145299382534311']);

    const already = screen
      .getAllByTestId('link-candidate')
      .find((el) => el.getAttribute('data-asset-id') === '727145299382534310') as HTMLElement;
    expect(already).toBeDisabled();

    fireEvent.click(
      screen
        .getAllByTestId('link-candidate')
        .find((el) => el.getAttribute('data-asset-id') === '727145299382534311') as HTMLElement,
    );
    expect(props.onAdd).toHaveBeenCalledWith('727145299382534311');
  });

  it('a failed search says so instead of reading as an empty library', async () => {
    listAssets.mockRejectedValueOnce(new Error('offline'));
    const { props } = renderSection();
    fireEvent.click(screen.getByTestId('relation-add'));
    await waitFor(() => expect(props.onError).toHaveBeenCalled());
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.queryByText('Nothing To Link Here Yet')).toBeNull();
  });

  it('the audio section links to the type its subtype allows', async () => {
    renderSection({
      detail: AUDIO_DETAIL,
      spec: relationSectionsFor('audio', 'sfx')[0],
      related: {},
    });
    fireEvent.click(screen.getByTestId('relation-add'));
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    expect(listAssets).toHaveBeenCalledWith(SCOPE_ID, {
      type: 'location',
      q: undefined,
      limit: 30,
    });
  });
});
