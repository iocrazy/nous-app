/**
 * `GenerateMissingDialog`: the preview the user reads before paying, the run,
 * and the three-part result.
 *
 * The result assertions are the ones that matter. A 202 from
 * `POST /generate-slot` carries `generation_ids`, `failed` (per unit) and
 * `skipped_references` — a run can produce two of four images AND silently
 * drop the asset's own reference picture, and reading only the id list reports
 * both as an unqualified success. Each of the three is pinned separately here,
 * because that is how they arrive.
 *
 * `generation_ids`, not `generations`: the wire name is asserted through the
 * client's real return shape.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { i18nMock, SCOPE_ID, UiModalStub } from './sheetTestUtils';

vi.mock('react-i18next', () => i18nMock);
vi.mock('../../../ui/primitives', () => ({ UiModal: UiModalStub }));

const addToast = vi.fn();
vi.mock('../../../Toast', () => ({ useToast: () => ({ addToast }) }));

const previewGenerateSlot = vi.fn();
const generateSlot = vi.fn();
vi.mock('../../../../services/assetsService', () => ({
  previewGenerateSlot: (...a: unknown[]) => previewGenerateSlot(...a),
  generateSlot: (...a: unknown[]) => generateSlot(...a),
}));

const saveGenerationAsAsset = vi.fn();
vi.mock('../../../../services/generatedService', () => ({
  saveGenerationAsAsset: (...a: unknown[]) => saveGenerationAsAsset(...a),
}));

// The catalog, from the same hook the canvas prompt node uses.
const models = vi.fn(() => [
  { name: 'seedream-4', display_name: 'Seedream 4', type: 'image' as const },
]);
vi.mock('../../../../features/canvas-core/smart/nodes/useGenerationModels', () => ({
  useGenerationModels: (...a: unknown[]) => models(...(a as [])),
}));

import { GenerateMissingDialog } from './GenerateMissingDialog';
import { CHARACTER_DETAIL } from './assetSheetFixtures';

/** The real `GET /generate-slot/preview` body. */
const PREVIEW = {
  positive: 'same woman as reference, character sheet: one chest-up close-up…',
  negative: 'text, watermark, cropped',
  reference_resource_ids: ['727145299382534146', '727145299382534150'],
  aspect_ratio: '16:9',
  model: 'seedream-4',
};

const onClose = vi.fn();
const onAttached = vi.fn();
const onOpenInbox = vi.fn();
const onError = vi.fn();

function renderDialog(props: Partial<React.ComponentProps<typeof GenerateMissingDialog>> = {}) {
  return render(
    <GenerateMissingDialog
      open
      scopeId={SCOPE_ID}
      detail={CHARACTER_DETAIL}
      slot="sheet"
      loadoutId={null}
      onClose={onClose}
      onAttached={onAttached}
      onOpenInbox={onOpenInbox}
      onError={onError}
      {...props}
    />,
  );
}

class ApiError extends Error {
  code: string;
  constructor(code: string) {
    super(code);
    this.code = code;
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  models.mockReturnValue([
    { name: 'seedream-4', display_name: 'Seedream 4', type: 'image' as const },
  ]);
  previewGenerateSlot.mockResolvedValue(PREVIEW);
  generateSlot.mockResolvedValue({
    generation_ids: ['800000000000000001', '800000000000000002'],
    failed: [],
    skipped_references: [],
    inbox_state: 'unreviewed',
  });
  saveGenerationAsAsset.mockResolvedValue({});
});

describe('the preview', () => {
  it('is fetched for this slot and loadout before anything can be paid for', async () => {
    renderDialog({ loadoutId: '727145299382534401' });
    await waitFor(() => expect(previewGenerateSlot).toHaveBeenCalled());
    expect(previewGenerateSlot).toHaveBeenCalledWith(
      SCOPE_ID,
      CHARACTER_DETAIL.id,
      'sheet',
      '727145299382534401',
    );
  });

  it('renders the positive prompt, the aspect and the reference COUNT', async () => {
    renderDialog();
    await screen.findByTestId('generate-preview');
    expect(screen.getByTestId('preview-positive')).toHaveTextContent('character sheet');
    expect(screen.getByTestId('preview-aspect')).toHaveTextContent('16:9');
    // The count, not the ids: `reference_resource_ids` is INTENT; what
    // actually went is the run's `skipped_references`.
    expect(screen.getByTestId('preview-references')).toHaveTextContent('2 Reference Images');
  });

  it('labels the negative as recorded, not as something the model reads', async () => {
    renderDialog();
    await screen.findByTestId('preview-negative');
    // No image adapter in this repo accepts a negative prompt; the dialog must
    // not imply that this text shapes the picture.
    expect(screen.getByTestId('generate-preview')).toHaveTextContent('Negative (Recorded)');
    expect(screen.getByTestId('generate-preview')).toHaveTextContent(
      'No image model here accepts one',
    );
  });

  it('seeds the model picker with the model the run would actually use', async () => {
    renderDialog();
    await screen.findByTestId('generate-model');
    expect(screen.getByTestId('generate-model')).toHaveValue('seedream-4');
  });

  it('keeps a preview model the catalog no longer offers', async () => {
    // An admin can disable the row that is still the catalog default. Snapping
    // the picker to blank would show a model other than the one that will run.
    models.mockReturnValue([]);
    renderDialog();
    await screen.findByTestId('generate-model');
    expect(screen.getByTestId('generate-model')).toHaveValue('seedream-4');
    expect(within(screen.getByTestId('generate-model')).getByText('seedream-4')).toBeTruthy();
  });

  it('asks the catalog for IMAGE models only', async () => {
    renderDialog();
    await screen.findByTestId('generate-model');
    expect(models).toHaveBeenCalledWith('image');
  });

  it('is fetched ONCE, not once per render', async () => {
    // react-i18next does not promise a stable `t` identity - the mock hands
    // back a fresh one every render, exactly as the real hook may. An effect
    // that depended on it would re-request the preview forever AND reset the
    // count picker under the user's hands. Interacting is what makes this
    // falsifiable: a render has to happen for the loop to show.
    renderDialog();
    await screen.findByTestId('generate-preview');
    fireEvent.click(screen.getAllByTestId('generate-count-option')[2]);
    fireEvent.change(screen.getByTestId('generate-model'), { target: { value: '' } });
    await waitFor(() => expect(previewGenerateSlot).toHaveBeenCalledTimes(1));
    // And the picker kept what the user chose.
    expect(screen.getAllByTestId('generate-count-option')[2]).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('a slot with no template is a typed refusal with Generate switched off', async () => {
    previewGenerateSlot.mockRejectedValue(new ApiError('slot_not_generatable'));
    renderDialog({ slot: 'worn' });
    const refusal = await screen.findByTestId('generate-refused');
    expect(refusal).toHaveAttribute('data-code', 'slot_not_generatable');
    expect(screen.getByTestId('generate-submit')).toBeDisabled();
    expect(screen.queryByTestId('generate-preview')).toBeNull();
  });
});

describe('the count', () => {
  it('offers exactly 1 to 4 — the server bound, mirrored', async () => {
    renderDialog();
    await screen.findByTestId('generate-count');
    const options = screen.getAllByTestId('generate-count-option');
    expect(options.map((o) => o.getAttribute('data-count'))).toEqual(['1', '2', '3', '4']);
  });

  it('defaults to one and sends what was chosen', async () => {
    renderDialog();
    await screen.findByTestId('generate-count');
    expect(screen.getAllByTestId('generate-count-option')[0]).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    fireEvent.click(screen.getAllByTestId('generate-count-option')[3]);
    fireEvent.click(screen.getByTestId('generate-submit'));
    await waitFor(() => expect(generateSlot).toHaveBeenCalled());
    expect(generateSlot).toHaveBeenCalledWith(SCOPE_ID, CHARACTER_DETAIL.id, {
      slot: 'sheet',
      loadout_id: null,
      model: 'seedream-4',
      count: 4,
    });
  });

  it('sends model null when the catalog default is chosen', async () => {
    renderDialog();
    await screen.findByTestId('generate-model');
    fireEvent.change(screen.getByTestId('generate-model'), { target: { value: '' } });
    fireEvent.click(screen.getByTestId('generate-submit'));
    await waitFor(() => expect(generateSlot).toHaveBeenCalled());
    expect(generateSlot.mock.calls[0][2].model).toBeNull();
  });
});

describe('the result is three things, not one', () => {
  it('states how many ids came back and links to the inbox', async () => {
    renderDialog();
    await screen.findByTestId('generate-preview');
    fireEvent.click(screen.getByTestId('generate-submit'));

    await screen.findByTestId('generate-result');
    expect(screen.getByTestId('result-count')).toHaveTextContent(
      '2 Images Are In The Generated Inbox',
    );
    expect(addToast).toHaveBeenCalledWith('Sent 2 To The Generated Inbox', 'success');

    fireEvent.click(screen.getByTestId('open-generated-inbox'));
    expect(onOpenInbox).toHaveBeenCalled();
  });

  it('lists the units that failed alongside the ones that did not', async () => {
    generateSlot.mockResolvedValue({
      generation_ids: ['800000000000000001'],
      failed: [
        { index: 1, code: 'generation_failed', detail: 'upstream 429' },
        { index: 2, code: 'register_failed', detail: 'store unavailable' },
      ],
      skipped_references: [],
      inbox_state: 'unreviewed',
    });
    renderDialog();
    await screen.findByTestId('generate-preview');
    fireEvent.click(screen.getByTestId('generate-submit'));

    const failed = await screen.findByTestId('result-failed');
    // "1 image" and "2 failed" are both true of the same run; reporting only
    // the first is how a partially-paid-for run reads as clean.
    expect(screen.getByTestId('result-count')).toHaveTextContent('1 Images');
    expect(within(failed).getAllByRole('listitem')).toHaveLength(2);
    expect(failed).toHaveTextContent('Image 2 failed');
    expect(failed).toHaveTextContent('Image 3 failed');
  });

  it('lists references that never reached the provider', async () => {
    generateSlot.mockResolvedValue({
      generation_ids: ['800000000000000001'],
      failed: [],
      skipped_references: [
        { resource_id: '727145299382534146', reason: 'file missing from the object store' },
      ],
      inbox_state: 'unreviewed',
    });
    renderDialog();
    await screen.findByTestId('generate-preview');
    fireEvent.click(screen.getByTestId('generate-submit'));

    const skipped = await screen.findByTestId('result-skipped');
    // The preview promised two references. A run that quietly sent one and
    // said nothing produces a picture that ignored the asset's own image.
    expect(skipped).toHaveTextContent('727145299382534146');
    expect(skipped).toHaveTextContent('file missing from the object store');
  });

  it('a wholly failed run is reported, not rendered as a result', async () => {
    // Every unit failing is a 503, never a 202 over an empty list.
    generateSlot.mockRejectedValue(new ApiError('generation_failed'));
    renderDialog();
    await screen.findByTestId('generate-preview');
    fireEvent.click(screen.getByTestId('generate-submit'));
    await waitFor(() => expect(onError).toHaveBeenCalled());
    expect(screen.queryByTestId('generate-result')).toBeNull();
  });
});

describe('Attach Now', () => {
  async function runOnce() {
    renderDialog();
    await screen.findByTestId('generate-preview');
    fireEvent.click(screen.getByTestId('generate-submit'));
    await screen.findByTestId('generate-result');
  }

  it('calls save-as-asset once per generated id, with the slot', async () => {
    await runOnce();
    fireEvent.click(screen.getByTestId('attach-now'));

    await waitFor(() => expect(saveGenerationAsAsset).toHaveBeenCalledTimes(2));
    expect(saveGenerationAsAsset).toHaveBeenNthCalledWith(1, SCOPE_ID, '800000000000000001', {
      asset_id: CHARACTER_DETAIL.id,
      slot: 'sheet',
      loadout_id: undefined,
    });
    expect(saveGenerationAsAsset).toHaveBeenNthCalledWith(2, SCOPE_ID, '800000000000000002', {
      asset_id: CHARACTER_DETAIL.id,
      slot: 'sheet',
      loadout_id: undefined,
    });
    await waitFor(() => expect(onAttached).toHaveBeenCalled());
  });

  it('carries the loadout on a worn slot', async () => {
    renderDialog({ slot: 'worn', loadoutId: '727145299382534401' });
    await screen.findByTestId('generate-preview');
    fireEvent.click(screen.getByTestId('generate-submit'));
    await screen.findByTestId('generate-result');
    fireEvent.click(screen.getByTestId('attach-now'));
    await waitFor(() => expect(saveGenerationAsAsset).toHaveBeenCalled());
    expect(saveGenerationAsAsset.mock.calls[0][2].loadout_id).toBe('727145299382534401');
  });

  it('reports a partial attach as partial', async () => {
    // Per-id, unlike the Equip batch: `save-as-asset` is one call per
    // generation, so a rejection on the second must not hide that the first
    // landed — nor be reported as if all of them did.
    saveGenerationAsAsset
      .mockResolvedValueOnce({})
      .mockRejectedValueOnce(new ApiError('invalid_slot'));
    await runOnce();
    fireEvent.click(screen.getByTestId('attach-now'));

    const outcome = await screen.findByTestId('result-attached');
    expect(outcome).toHaveTextContent('1 attached, 1 refused');
    expect(onAttached).toHaveBeenCalledTimes(1);
    expect(addToast).toHaveBeenCalledWith('1 Could Not Be Attached', 'error');
  });

  it('does not tell the sheet to refetch when nothing landed', async () => {
    saveGenerationAsAsset.mockRejectedValue(new ApiError('invalid_slot'));
    await runOnce();
    fireEvent.click(screen.getByTestId('attach-now'));
    await screen.findByTestId('result-attached');
    expect(onAttached).not.toHaveBeenCalled();
  });
});
