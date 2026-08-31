/**
 * The prompt block: which key a field PATCHes, and what the two agent buttons
 * do with their answer.
 *
 * "Only what changed" is the assertion worth having. `AssetUpdate` is
 * `exclude_unset`: a body assembled from the whole form would materialize
 * `prompt_negative`, `prompt_positive_zh` and `prompt_negative_zh` as well,
 * turning three untouched fields into three rewritten ones - a silent
 * overwrite with a 200 on it.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { i18nMock, SCOPE_ID } from './sheetTestUtils';

vi.mock('react-i18next', () => i18nMock);
vi.mock('../../../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `/api/v1/resources/${id}/cover`,
}));

const updateAsset = vi.fn();
const translatePrompt = vi.fn();
const regeneratePrompt = vi.fn();
vi.mock('../../../../services/assetsService', () => ({
  updateAsset: (...a: unknown[]) => updateAsset(...a),
  translatePrompt: (...a: unknown[]) => translatePrompt(...a),
  regeneratePrompt: (...a: unknown[]) => regeneratePrompt(...a),
}));

import { PromptEditor } from './PromptEditor';
import { CHARACTER_DETAIL, PRESET_PROMPT_DETAIL, makeDetail, makeFile } from './assetSheetFixtures';

function renderEditor(overrides: Partial<React.ComponentProps<typeof PromptEditor>> = {}) {
  const props = {
    scopeId: SCOPE_ID,
    detail: CHARACTER_DETAIL,
    readOnly: false,
    showPromptExtras: false,
    onDetailUpdated: vi.fn(),
    onChanged: vi.fn(),
    onError: vi.fn(),
    ...overrides,
  };
  return { ...render(<PromptEditor {...props} />), props };
}

beforeEach(() => {
  updateAsset.mockReset().mockResolvedValue({});
  translatePrompt.mockReset().mockResolvedValue(CHARACTER_DETAIL);
  regeneratePrompt.mockReset().mockResolvedValue(CHARACTER_DETAIL);
});

describe('editing', () => {
  it('saves only the field that changed, on blur', async () => {
    renderEditor();
    const positive = screen.getByTestId('prompt-positive');
    fireEvent.change(positive, { target: { value: 'weathered, mid-thirties' } });
    fireEvent.blur(positive);
    await waitFor(() => expect(updateAsset).toHaveBeenCalledTimes(1));
    expect(updateAsset).toHaveBeenCalledWith(SCOPE_ID, CHARACTER_DETAIL.id, {
      prompt_positive: 'weathered, mid-thirties',
    });
  });

  it('an untouched field sends nothing', () => {
    renderEditor();
    fireEvent.blur(screen.getByTestId('prompt-positive'));
    fireEvent.blur(screen.getByTestId('prompt-negative'));
    expect(updateAsset).not.toHaveBeenCalled();
  });

  it('clearing a field sends null, not an empty string', async () => {
    // null CLEARS the column; "" would store an empty string that reads as
    // "there is a prompt here, and it is blank".
    renderEditor();
    const positive = screen.getByTestId('prompt-positive');
    fireEvent.change(positive, { target: { value: '   ' } });
    fireEvent.blur(positive);
    await waitFor(() => expect(updateAsset).toHaveBeenCalled());
    expect(updateAsset).toHaveBeenCalledWith(SCOPE_ID, CHARACTER_DETAIL.id, {
      prompt_positive: null,
    });
  });

  it('the ZH tab edits the ZH columns', async () => {
    renderEditor();
    fireEvent.click(
      screen.getAllByTestId('prompt-lang').find((el) => el.dataset.lang === 'zh') as HTMLElement,
    );
    const positive = screen.getByTestId('prompt-positive');
    fireEvent.change(positive, { target: { value: 'x' } });
    fireEvent.blur(positive);
    await waitFor(() => expect(updateAsset).toHaveBeenCalled());
    expect(updateAsset).toHaveBeenCalledWith(SCOPE_ID, CHARACTER_DETAIL.id, {
      prompt_positive_zh: 'x',
    });
  });

  it('a preset renders the prompt disabled with no agent buttons', () => {
    renderEditor({ detail: PRESET_PROMPT_DETAIL, readOnly: true, showPromptExtras: true });
    expect(screen.getByTestId('prompt-positive')).toBeDisabled();
    expect(screen.queryByTestId('prompt-translate')).toBeNull();
    expect(screen.queryByTestId('prompt-regenerate')).toBeNull();
  });
});

describe('agent actions', () => {
  it('Translate targets the language NOT on screen', async () => {
    const { props } = renderEditor();
    fireEvent.click(screen.getByTestId('prompt-translate'));
    await waitFor(() => expect(translatePrompt).toHaveBeenCalled());
    expect(translatePrompt).toHaveBeenCalledWith(SCOPE_ID, CHARACTER_DETAIL.id, 'zh');
    // Both endpoints answer with the written asset, so the sheet takes their
    // answer rather than guessing what they wrote.
    await waitFor(() => expect(props.onDetailUpdated).toHaveBeenCalledWith(CHARACTER_DETAIL));
  });

  it('Regenerate reads the primary file', async () => {
    renderEditor();
    fireEvent.click(screen.getByTestId('prompt-regenerate'));
    await waitFor(() => expect(regeneratePrompt).toHaveBeenCalledWith(SCOPE_ID, CHARACTER_DETAIL.id));
  });

  it('a typed refusal is reported, not swallowed', async () => {
    // 503 `translate_unavailable` / 422 `nothing_to_translate` arrive here.
    translatePrompt.mockRejectedValueOnce(new Error('503'));
    const { props } = renderEditor();
    fireEvent.click(screen.getByTestId('prompt-translate'));
    await waitFor(() => expect(props.onError).toHaveBeenCalledTimes(1));
    expect(props.onDetailUpdated).not.toHaveBeenCalled();
  });
});

describe('prompt-type extras', () => {
  const promptDetail = makeDetail({
    ...PRESET_PROMPT_DETAIL,
    id: PRESET_PROMPT_DETAIL.id,
    scope_id: '727145299382534200',
    is_system_preset: false,
    source: 'duplicated',
    files: [makeFile({ resource_id: 'ex1', slot: 'examples', asset_id: PRESET_PROMPT_DETAIL.id })],
  });

  it('shows placeholders, platform params and Examples only for prompt assets', () => {
    renderEditor({ detail: promptDetail, showPromptExtras: true });
    expect(screen.getByTestId('prompt-placeholders')).toHaveTextContent('{{subject}}');
    expect(screen.getByTestId('platform-params')).toBeTruthy();
    expect(screen.getByTestId('example-pin')).toHaveAttribute('data-resource-id', 'ex1');
  });

  it('hides all three on every other type', () => {
    renderEditor();
    expect(screen.queryByTestId('prompt-placeholders')).toBeNull();
    expect(screen.queryByTestId('platform-params')).toBeNull();
    expect(screen.queryByTestId('prompt-examples')).toBeNull();
  });

  it('saves a platform param, keeping a string a string', async () => {
    renderEditor({ detail: promptDetail, showPromptExtras: true });
    const value = screen.getByTestId('param-value');
    fireEvent.change(value, { target: { value: '16:9' } });
    fireEvent.blur(value);
    await waitFor(() => expect(updateAsset).toHaveBeenCalled());
    // "16:9" is not JSON, so it stays text. Guessing types would turn a model
    // named "4" into the number 4.
    expect(updateAsset).toHaveBeenCalledWith(SCOPE_ID, promptDetail.id, {
      platform_params: { aspect_ratio: '16:9' },
    });
  });

  it('an Examples pin opens the lightbox', () => {
    renderEditor({ detail: promptDetail, showPromptExtras: true });
    fireEvent.click(screen.getByTestId('example-pin'));
    expect(screen.getByTestId('pin-lightbox-image')).toHaveAttribute('data-resource-id', 'ex1');
  });
});
