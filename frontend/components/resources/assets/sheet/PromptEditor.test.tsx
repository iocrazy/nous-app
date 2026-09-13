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
  getResourceFileUrl: (id: string) => `/api/v1/resources/${id}/file`,
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
    // "16:9" is not JSON, so it stays text.
    expect(updateAsset).toHaveBeenCalledWith(SCOPE_ID, promptDetail.id, {
      platform_params: { aspect_ratio: '16:9' },
    });
  });

  it('a blur that changed nothing is not a request', async () => {
    // Tabbing through the panel must not PATCH the column or bump
    // `updated_at` - the same rule the inline text fields follow.
    renderEditor({ detail: promptDetail, showPromptExtras: true });
    fireEvent.blur(screen.getByTestId('param-key'));
    fireEvent.blur(screen.getByTestId('param-value'));
    await Promise.resolve();
    expect(updateAsset).not.toHaveBeenCalled();
  });

  it('does not retype a stored string on the way back out', async () => {
    // `{"model": "4"}` renders as the text `4`. Re-reading every row from its
    // text would rewrite the column to `{"model": 4}` - a silent retype of a
    // value the user never touched. Note "16:9" cannot catch this: it throws
    // in JSON.parse and so survives the buggy path too.
    const withNumericString = makeDetail({
      ...promptDetail,
      id: promptDetail.id,
      platform_params: { model: '4', steps: 30 },
    });
    renderEditor({ detail: withNumericString, showPromptExtras: true });

    // Edit the OTHER row, so a save happens and carries the untouched one.
    const values = screen.getAllByTestId('param-value');
    fireEvent.change(values[1], { target: { value: '40' } });
    fireEvent.blur(values[1]);

    await waitFor(() => expect(updateAsset).toHaveBeenCalled());
    expect(updateAsset).toHaveBeenCalledWith(SCOPE_ID, promptDetail.id, {
      platform_params: { model: '4', steps: 40 },
    });
  });

  it('an edited row IS re-read, so a typed number becomes one', async () => {
    const withNumericString = makeDetail({
      ...promptDetail,
      id: promptDetail.id,
      platform_params: { model: '4' },
    });
    renderEditor({ detail: withNumericString, showPromptExtras: true });
    const value = screen.getByTestId('param-value');
    fireEvent.change(value, { target: { value: '5' } });
    fireEvent.blur(value);
    await waitFor(() => expect(updateAsset).toHaveBeenCalled());
    expect(updateAsset).toHaveBeenCalledWith(SCOPE_ID, promptDetail.id, {
      platform_params: { model: 5 },
    });
  });

  it('an Examples pin opens the lightbox', () => {
    renderEditor({ detail: promptDetail, showPromptExtras: true });
    fireEvent.click(screen.getByTestId('example-pin'));
    expect(screen.getByTestId('pin-lightbox-image')).toHaveAttribute('data-resource-id', 'ex1');
  });
});

/**
 * Field height.
 *
 * The complaint this answers: a preset's positive prompt runs well past the
 * three rows the box was fixed at, so the sheet showed a sliver of it behind
 * an inner scrollbar. `resize-y` was already on the class list, but a handle
 * nobody finds is not an answer — and having to drag it open on every visit
 * is not one either.
 *
 * The contract is MIN-height, not height: the box is at least as tall as its
 * own content up to a cap, and a manual drag can always make it taller. Using
 * `height` would have fought the drag on the next keystroke.
 *
 * jsdom computes no layout, so `scrollHeight` is stubbed — these tests pin the
 * ARITHMETIC (measure, cap, re-measure on change), which is the part that can
 * be wrong. That the browser lays it out is not in question.
 */
describe('prompt field height', () => {
  const stubScrollHeight = (px: number) =>
    Object.defineProperty(HTMLTextAreaElement.prototype, 'scrollHeight', {
      configurable: true,
      get: () => px,
    });

  it('grows to fit its content instead of clipping it', () => {
    stubScrollHeight(300);
    renderEditor();
    expect((screen.getByTestId('prompt-positive') as HTMLTextAreaElement).style.minHeight).toBe('300px');
  });

  // Without a cap, one very long prompt pushes the platform params and the
  // Examples row off the bottom of the page, and the sheet stops being a
  // sheet.
  it('stops growing at the cap and lets the rest scroll', () => {
    stubScrollHeight(4000);
    renderEditor();
    expect((screen.getByTestId('prompt-positive') as HTMLTextAreaElement).style.minHeight).toBe('480px');
  });

  // The ratchet this guards: measuring without first releasing the min-height
  // reads back the height we ourselves set, so a field could grow but never
  // shrink again when its text was deleted.
  it('shrinks again when the text is deleted', () => {
    stubScrollHeight(300);
    renderEditor();
    const field = screen.getByTestId('prompt-positive') as HTMLTextAreaElement;
    expect(field.style.minHeight).toBe('300px');
    stubScrollHeight(60);
    fireEvent.change(field, { target: { value: 'x' } });
    expect(field.style.minHeight).toBe('60px');
  });
});
