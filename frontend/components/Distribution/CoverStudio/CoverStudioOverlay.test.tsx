/**
 * The overlay shell. Children are tested in their own files; this file pins
 * only what the ASSEMBLY owns:
 *
 *   1. Generate is blocked until there is a topic AND a person reference, and
 *      the hint names which one is missing. The person slot is a blocker, not
 *      a warning — generating without it produces a different face per draft,
 *      which reads as "the style is broken".
 *   2. A grab lands as a frame normally, as the PERSON when the checkbox is
 *      ticked — and the checkbox unticks itself after, so the next grab does
 *      not silently replace the person.
 *   3. Stage 2 sends person + grid as the references — not the whole pool.
 *      The stage-2 prompt speaks of exactly those two pictures; re-sending
 *      frames and templates would spend slots on pictures it never mentions.
 *   4. Apply hands the parent the PROMOTED resource id, not the generated-media
 *      id. The cover slot on the publish page references `resources` rows; the
 *      gen id would 404 at publish time.
 *   5. Template usage ticks once per successful dispatch, with template ids
 *      only.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import enJson from '../../../public/locales/en.json';
import type { LibraryVideo } from '../../../types';

const svc = vi.hoisted(() => ({
  generateCoverDrafts: vi.fn(),
  refineCoverDraft: vi.fn(),
  awaitCoverGeneration: vi.fn(),
  promoteGeneration: vi.fn(),
  markCoverTemplatesUsed: vi.fn(),
  resolveCoverTemplateReference: vi.fn(),
  saveGeneratedCoverAsTemplate: vi.fn(),
  listGenerationModels: vi.fn(),
  listCoverStyles: vi.fn(),
  listCoverRounds: vi.fn(),
  selectCoverFrame: vi.fn(),
  uploadResource: vi.fn(),
  importCanvasMedia: vi.fn(),
}));
vi.mock('../../../services/distributionService', () => ({
  selectCoverFrame: svc.selectCoverFrame,
}));
vi.mock('../../../services/resourceService', () => ({
  uploadResource: svc.uploadResource,
}));
vi.mock('../../../features/canvas-core/smart/mediaImport', () => ({
  importCanvasMedia: svc.importCanvasMedia,
}));

vi.mock('../../../services/coverStudioService', () => ({
  BUILTIN_COVER_STYLE: 'viral-video-cover',
  generateCoverDrafts: svc.generateCoverDrafts,
  refineCoverDraft: svc.refineCoverDraft,
  awaitCoverGeneration: svc.awaitCoverGeneration,
  listCoverStyles: svc.listCoverStyles,
  listCoverRounds: svc.listCoverRounds,
  genIdFromUrl: (url: string | undefined) => url?.match(/\/generated-media\/(\d+)\//)?.[1] ?? null,
}));

const BUILTIN_STYLE = {
  slug: 'viral-video-cover',
  name: 'Viral Video Cover',
  description: 'Dark background, one huge headline, one strong face.',
  requires_person: true,
  aspects: ['3:4', '4:3'],
  builtin: true,
};
const NEON_STYLE = {
  slug: 'neon-cover',
  name: 'Neon Cover',
  description: 'Neon palette, no person needed.',
  requires_person: false,
  aspects: ['3:4', '4:3'],
  builtin: false,
};
vi.mock('../../../services/generatedMediaService', () => ({
  promoteGeneration: svc.promoteGeneration,
}));
vi.mock('../../../services/coverTemplateService', () => ({
  markCoverTemplatesUsed: svc.markCoverTemplatesUsed,
  resolveCoverTemplateReference: svc.resolveCoverTemplateReference,
  saveGeneratedCoverAsTemplate: svc.saveGeneratedCoverAsTemplate,
}));
vi.mock('../../../features/canvas-core/services/canvasGenerationService', () => ({
  listGenerationModels: svc.listGenerationModels,
}));
vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

// The grabber and template grid have their own suites; here they are reduced
// to the one signal each feeds the shell.
vi.mock('./CoverFrameGrabber', () => ({
  CoverFrameGrabber: ({
    onGrabbed,
    onUseAsCover,
    aspect,
    onPickSource,
  }: {
    onGrabbed: (f: unknown, asPerson: boolean) => void;
    onUseAsCover?: (sourceId: string, at: number, focus: { x: number; y: number }) => Promise<void>;
    aspect?: string;
    onPickSource?: (v: unknown) => void;
  }) => (
    <>
    <button
      type="button"
      data-testid="stub-pick-source"
      onClick={() => onPickSource?.({ id: '902', filename: 'talk.mp4', thumbnail_url: null })}
    >
      pick
    </button>
    <button
      type="button"
      data-testid="stub-use-frame"
      data-aspect={aspect}
      onClick={() => void onUseAsCover?.('900', 3.1, { x: 0.25, y: 0.75 })}
    >
      use
    </button>
    <button
      type="button"
      data-testid="stub-grab"
      onClick={() =>
        onGrabbed({
          generatedMediaId: '111',
          url: '/api/v1/generated-media/111/cover',
          timestampSeconds: 3.1,
        }, false)
      }
    >
      grab
    </button>
    <button
      type="button"
      data-testid="stub-grab-person"
      onClick={() =>
        onGrabbed({
          generatedMediaId: '111',
          url: '/api/v1/generated-media/111/cover',
          timestampSeconds: 3.1,
        }, true)
      }
    >
      person
    </button>
    </>
  ),
}));
vi.mock('./CoverTemplatePickerModal', () => ({
  CoverTemplatePickerModal: ({ open, onPick, onClose }: { open: boolean; onPick: (t: unknown[]) => void; onClose: () => void }) =>
    open ? (
      <button
        type="button"
        data-testid="stub-template"
        onClick={() => {
          onPick([
            {
              resource_id: 'tpl-7',
              name: 'Bold headline',
              mime_type: 'image/png',
              thumb_url: '/api/v1/resources/tpl-7/cover',
              usage_count: 0,
              last_used_at: null,
            },
          ]);
          onClose();
        }}
      >
        tpl
      </button>
    ) : null,
}));

import { CoverStudioOverlay } from './CoverStudioOverlay';

const VIDEO: LibraryVideo = { id: '900', filename: 'clip-a.mp4', thumbnail_url: null };

const makeI18n = (): I18n => {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
  });
  return inst;
};

function renderOverlay(over: Partial<React.ComponentProps<typeof CoverStudioOverlay>> = {}) {
  const onClose = vi.fn();
  const onApply = vi.fn();
  render(
    <I18nextProvider i18n={makeI18n()}>
      <CoverStudioOverlay
        open
        scopeId="42"
        sources={[VIDEO]}
        topic="三分钟看懂内容差"
        onClose={onClose}
        onApply={onApply}
        {...over}
      />
    </I18nextProvider>,
  );
  return { onClose, onApply };
}

/** Open the library picker from the pool tile and take the stub's template. */
async function pickTemplate() {
  fireEvent.click(screen.getByTestId('cover-ref-add-template'));
  fireEvent.click(await screen.findByTestId('stub-template'));
  await screen.findByText('Bold headline');
}

/** "Set as person" on the stage — the shortest path to "person set". */
function setPerson() {
  fireEvent.click(screen.getByTestId('stub-grab-person'));
}

beforeEach(() => {
  vi.clearAllMocks();
  svc.listGenerationModels.mockResolvedValue([]);
  svc.listCoverStyles.mockResolvedValue([BUILTIN_STYLE, NEON_STYLE]);
  svc.listCoverRounds.mockResolvedValue([]);
  svc.generateCoverDrafts.mockResolvedValue({
    task_id: 't1',
    prompt: 'STAGE1 PROMPT',
    aspect: '3:4',
    reference_count: 1,
  });
  svc.refineCoverDraft.mockResolvedValue({
    task_id: 't2',
    prompt: 'STAGE2 PROMPT',
    aspect: '3:4',
    reference_count: 2,
  });
  svc.awaitCoverGeneration.mockResolvedValue({
    ok: true,
    url: '/api/v1/generated-media/500/cover',
    generatedMediaId: '500',
  });
  svc.promoteGeneration.mockResolvedValue({ promoted_resource_id: '9000' });
  svc.resolveCoverTemplateReference.mockResolvedValue({
    genId: '777',
    url: '/api/v1/generated-media/777/cover',
  });
  svc.saveGeneratedCoverAsTemplate.mockResolvedValue({
    resourceId: '9000',
    template: { resource_id: '9000', name: 'Cover', mime_type: 'image/png', thumb_url: '/api/v1/resources/9000/cover', usage_count: 0, last_used_at: null },
  });
  svc.markCoverTemplatesUsed.mockResolvedValue(undefined);
  svc.selectCoverFrame.mockResolvedValue({
    cover_vertical_resource_id: '7001',
    cover_horizontal_resource_id: '7002',
  });
});

describe('CoverStudioOverlay — gating', () => {
  it('blocks generation without a person and says so', () => {
    renderOverlay();

    const btn = screen.getByTestId('cover-generate') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByTestId('cover-generate-hint').textContent).toMatch(
      /add the person picture first/,
    );
    fireEvent.click(btn);
    expect(svc.generateCoverDrafts).not.toHaveBeenCalled();
  });

  it('blocks generation without a topic and says so', () => {
    renderOverlay({ topic: '   ' });

    expect((screen.getByTestId('cover-generate') as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(screen.getByTestId('cover-generate-hint').textContent).toMatch(
      /give the publish a title, or write the prompt/,
    );
  });

  it('unblocks once a person is grabbed', () => {
    renderOverlay();
    setPerson();

    expect((screen.getByTestId('cover-generate') as HTMLButtonElement).disabled).toBe(
      false,
    );
  });
});


describe('CoverStudioOverlay — the two-stage run', () => {
  it('sends the pool with the person first, then shows the grid', async () => {
    renderOverlay();
    setPerson();
    await pickTemplate();

    fireEvent.click(screen.getByTestId('cover-generate'));

    await waitFor(() =>
      expect(svc.generateCoverDrafts).toHaveBeenCalledWith(
        expect.objectContaining({
          topic: '三分钟看懂内容差',
          sourceUrls: [
            '/api/v1/generated-media/111/cover',
            '/api/v1/generated-media/777/cover',
          ],
        }),
      ),
    );
    // Usage ticked once, with template ids only — not the person, not frames.
    expect(svc.markCoverTemplatesUsed).toHaveBeenCalledTimes(1);
    expect(svc.markCoverTemplatesUsed).toHaveBeenCalledWith(['tpl-7']);
  });

  it('stage 2 sends person + grid, not the whole pool', async () => {
    renderOverlay();
    setPerson();
    await pickTemplate();
    fireEvent.click(screen.getByTestId('cover-generate'));
    await waitFor(() => expect(screen.queryByTestId('cover-draft-2')).toBeTruthy());

    fireEvent.click(screen.getByTestId('cover-draft-2'));
    svc.awaitCoverGeneration.mockResolvedValueOnce({
      ok: true,
      url: '/api/v1/generated-media/600/cover',
      generatedMediaId: '600',
    });
    fireEvent.click(screen.getByTestId('cover-make-final'));

    await waitFor(() =>
      expect(svc.refineCoverDraft).toHaveBeenCalledWith(
        expect.objectContaining({
          selectedDraft: 2,
          // Person + the grid. The template is NOT re-sent — the stage-2
          // prompt never mentions it.
          sourceUrls: [
            '/api/v1/generated-media/111/cover',
            '/api/v1/generated-media/500/cover',
          ],
        }),
      ),
    );
  });

  it('surfaces a failed stage 1 and returns to idle', async () => {
    svc.awaitCoverGeneration.mockResolvedValueOnce({
      ok: false,
      error: 'no_credit: quota exhausted',
    });
    renderOverlay();
    setPerson();

    fireEvent.click(screen.getByTestId('cover-generate'));

    expect(await screen.findByTestId('cover-drafts-error')).toBeTruthy();
    expect(screen.getByTestId('cover-drafts-error').textContent).toContain('no_credit');
    // Recoverable: the button is usable again.
    expect((screen.getByTestId('cover-generate') as HTMLButtonElement).disabled).toBe(
      false,
    );
  });
});

describe('CoverStudioOverlay — apply', () => {
  async function runToDone() {
    renderOverlay();
    setPerson();
    fireEvent.click(screen.getByTestId('cover-generate'));
    await waitFor(() => expect(screen.queryByTestId('cover-draft-1')).toBeTruthy());
    fireEvent.click(screen.getByTestId('cover-draft-1'));
    svc.awaitCoverGeneration.mockResolvedValueOnce({
      ok: true,
      url: '/api/v1/generated-media/600/cover',
      generatedMediaId: '600',
    });
    fireEvent.click(screen.getByTestId('cover-make-final'));
    await screen.findByTestId('cover-apply');
  }

  it('hands the parent the PROMOTED resource id and closes', async () => {
    const { onApply, onClose } = { onApply: vi.fn(), onClose: vi.fn() };
    render(
      <I18nextProvider i18n={makeI18n()}>
        <CoverStudioOverlay
          open
          scopeId="42"
          sources={[VIDEO]}
          topic="x"
          onClose={onClose}
          onApply={onApply}
        />
      </I18nextProvider>,
    );
    setPerson();
    fireEvent.click(screen.getByTestId('cover-generate'));
    await waitFor(() => expect(screen.queryByTestId('cover-draft-1')).toBeTruthy());
    fireEvent.click(screen.getByTestId('cover-draft-1'));
    svc.awaitCoverGeneration.mockResolvedValueOnce({
      ok: true,
      url: '/api/v1/generated-media/600/cover',
      generatedMediaId: '600',
    });
    fireEvent.click(screen.getByTestId('cover-make-final'));
    await screen.findByTestId('cover-apply');

    fireEvent.click(screen.getByTestId('cover-apply'));

    await waitFor(() => expect(svc.promoteGeneration).toHaveBeenCalledWith('600'));
    // The RESOURCE id — the publish task references resources rows; the gen
    // id would 404 at publish time.
    await waitFor(() => expect(onApply).toHaveBeenCalledWith({ vertical: '9000' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('keeps the overlay open and says so when promote fails', async () => {
    svc.promoteGeneration.mockRejectedValueOnce(new Error('storage down'));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    await runToDone();

    fireEvent.click(screen.getByTestId('cover-apply'));

    expect(await screen.findByText(/could not be saved to your library/)).toBeTruthy();
    // Not closed — closing on failure looks exactly like success.
    expect(screen.getByTestId('cover-studio-overlay')).toBeTruthy();
    spy.mockRestore();
  });

  it('saves the final cover as a template on request', async () => {
    await runToDone();

    fireEvent.click(screen.getByTestId('cover-save-template'));

    // Promote → link into the folder; nothing was promoted yet, so no
    // resource id is passed for reuse.
    await waitFor(() =>
      expect(svc.saveGeneratedCoverAsTemplate).toHaveBeenCalledWith(
        '600',
        expect.any(String),
        undefined,
      ),
    );
    expect(await screen.findByText('Saved as template')).toBeTruthy();
  });
});

describe('CoverStudioOverlay — dialog chrome', () => {
  it('closes when the scrim is clicked, like SettingsModal', () => {
    const onClose = vi.fn();
    render(
      <I18nextProvider i18n={makeI18n()}>
        <CoverStudioOverlay open scopeId="42" sources={[VIDEO]} topic="x" onClose={onClose} onApply={vi.fn()} />
      </I18nextProvider>,
    );

    fireEvent.click(screen.getByTestId('cover-studio-scrim'));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes from the corner X', () => {
    const onClose = vi.fn();
    render(
      <I18nextProvider i18n={makeI18n()}>
        <CoverStudioOverlay open scopeId="42" sources={[VIDEO]} topic="x" onClose={onClose} onApply={vi.fn()} />
      </I18nextProvider>,
    );

    fireEvent.click(screen.getByTestId('cover-studio-back'));

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});


describe('CoverStudioOverlay — v4 layout', () => {
  it('sends the creator’s prompt-box sentence with stage 1', async () => {
    renderOverlay();
    setPerson();
    fireEvent.change(screen.getByTestId('cover-instructions'), {
      target: { value: '  人物指向右侧的大屏幕  ' },
    });

    fireEvent.click(screen.getByTestId('cover-generate'));

    await waitFor(() =>
      expect(svc.generateCoverDrafts).toHaveBeenCalledWith(
        expect.objectContaining({ instructions: '人物指向右侧的大屏幕' }),
      ),
    );
  });

  it('the vertical tab’s frame button fills the VERTICAL slot from the 3:4 crop', async () => {
    const { onApply, onClose } = renderOverlay();
    expect(screen.getByTestId('stub-use-frame').getAttribute('data-aspect')).toBe('3:4');

    fireEvent.click(screen.getByTestId('stub-use-frame'));

    await waitFor(() =>
      expect(svc.selectCoverFrame).toHaveBeenCalledWith({
        source_resource_id: '900',
        timestamp_seconds: 3.1,
        focus_x: 0.25,
        focus_y: 0.75,
        zoom: 1,
      }),
    );
    await waitFor(() => expect(onApply).toHaveBeenCalledWith({ vertical: '7001' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('the horizontal tab crops 4:3 and fills the HORIZONTAL slot', async () => {
    const { onApply } = renderOverlay();

    fireEvent.click(screen.getByTestId('cover-tab-horizontal'));
    expect(screen.getByTestId('stub-use-frame').getAttribute('data-aspect')).toBe('4:3');

    fireEvent.click(screen.getByTestId('stub-use-frame'));

    await waitFor(() => expect(onApply).toHaveBeenCalledWith({ horizontal: '7002' }));
  });

  it('the horizontal tab ALSO goes through the model, as 4:3, and "Done" fills the horizontal slot', async () => {
    const { onApply } = renderOverlay();
    fireEvent.click(screen.getByTestId('cover-tab-horizontal'));
    setPerson();

    fireEvent.click(screen.getByTestId('cover-generate'));

    await waitFor(() =>
      expect(svc.generateCoverDrafts).toHaveBeenCalledWith(
        expect.objectContaining({ aspect: '4:3', style: 'viral-video-cover' }),
      ),
    );
    await waitFor(() => expect(screen.queryByTestId('cover-draft-2')).toBeTruthy());
    expect(screen.getByTestId('cover-draft-grid').getAttribute('data-aspect')).toBe('4:3');

    fireEvent.click(screen.getByTestId('cover-draft-2'));
    svc.awaitCoverGeneration.mockResolvedValueOnce({
      ok: true,
      url: '/api/v1/generated-media/600/cover',
      generatedMediaId: '600',
    });
    fireEvent.click(screen.getByTestId('cover-make-final'));
    await screen.findByTestId('cover-apply');
    expect(svc.refineCoverDraft).toHaveBeenCalledWith(expect.objectContaining({ aspect: '4:3' }));

    fireEvent.click(screen.getByTestId('cover-apply'));
    await waitFor(() => expect(onApply).toHaveBeenCalledWith({ horizontal: '9000' }));
  });

  it('rounds are kept per tab — a vertical round does not show under horizontal', async () => {
    renderOverlay();
    setPerson();
    fireEvent.click(screen.getByTestId('cover-generate'));
    await waitFor(() => expect(screen.queryByTestId('cover-history-round-1')).toBeTruthy());

    fireEvent.click(screen.getByTestId('cover-tab-horizontal'));
    expect(screen.queryByTestId('cover-history-round-1')).toBeNull();
    expect(screen.getByTestId('cover-history-empty')).toBeTruthy();

    fireEvent.click(screen.getByTestId('cover-tab-vertical'));
    expect(screen.getByTestId('cover-history-round-1')).toBeTruthy();
  });

  it('lists the styles from the server and sends the chosen slug', async () => {
    renderOverlay();
    setPerson();
    const select = (await screen.findByTestId('cover-style')) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(2));
    expect(select.options[0].value).toBe('viral-video-cover');

    fireEvent.change(select, { target: { value: 'neon-cover' } });
    // The description lives in the (?) tip next to the label now.
    const tips = screen.getAllByTestId('cs-help').map((el) => el.getAttribute('title') ?? '');
    expect(tips.some((tip) => tip.includes('Neon palette'))).toBe(true);
    fireEvent.click(screen.getByTestId('cover-generate'));

    await waitFor(() =>
      expect(svc.generateCoverDrafts).toHaveBeenCalledWith(
        expect.objectContaining({ style: 'neon-cover' }),
      ),
    );
  });

  it('a style that needs no person is not blocked by the missing person', async () => {
    renderOverlay();
    const select = (await screen.findByTestId('cover-style')) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(2));
    expect((screen.getByTestId('cover-generate') as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(select, { target: { value: 'neon-cover' } });

    expect((screen.getByTestId('cover-generate') as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByTestId('cover-ref-person')).toBeNull();
  });

  it('keeps every round and goes back to an earlier one without regenerating', async () => {
    renderOverlay();
    setPerson();

    fireEvent.click(screen.getByTestId('cover-generate'));
    await waitFor(() => expect(screen.queryByTestId('cover-draft-1')).toBeTruthy());
    expect(screen.getByTestId('cover-history-round-1')).toBeTruthy();

    // A second round replaces the stage but not the history.
    svc.awaitCoverGeneration.mockResolvedValueOnce({
      ok: true,
      url: '/api/v1/generated-media/501/cover',
      generatedMediaId: '501',
    });
    fireEvent.click(screen.getByTestId('cover-generate'));
    await waitFor(() => expect(screen.queryByTestId('cover-history-round-2')).toBeTruthy());
    expect(svc.generateCoverDrafts).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByTestId('cover-history-round-1'));

    // Back on round 1's grid, and nothing was generated again.
    expect(screen.getByTestId('cover-draft-1')).toBeTruthy();
    expect(svc.generateCoverDrafts).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('cover-history-round-1').className).toContain('on');
  });
});


describe('CoverStudioOverlay — merged references card and in-studio video pick', () => {
  it('shows the nine slots with a "From library" tile that opens the picker', () => {
    renderOverlay();
    const card = screen.getByTestId('cover-ref-card');
    expect(card.querySelector('[data-testid="cover-ref-pool"]')).toBeTruthy();
    // The library is a picker now: the tile opens it, nothing is listed inline.
    expect(screen.queryByTestId('stub-template')).toBeNull();
    expect(card.querySelector('[data-testid="cover-ref-add-template"]')).toBeTruthy();
    expect(screen.getByText('Sent to the model')).toBeTruthy();
  });

  it('hands a video picked inside the studio up to the publish page', () => {
    const onPickSource = vi.fn();
    renderOverlay({ sources: [], onPickSource });

    fireEvent.click(screen.getByTestId('stub-pick-source'));

    expect(onPickSource).toHaveBeenCalledWith(expect.objectContaining({ id: '902' }));
  });
});


describe('CoverStudioOverlay — the person card', () => {
  it('is its own card, filled by "Set as person" on the stage, not one of the nine slots', () => {
    renderOverlay();
    const card = screen.getByTestId('cover-person-card');
    expect(card.querySelector('[data-testid="cover-ref-person"]')?.className).toContain('missing');
    expect(screen.getByTestId('cover-ref-count').textContent).toContain('0 / 9');

    setPerson();

    expect(card.querySelector('img')).toBeTruthy();
    // The person is sent too (the server cap of nine includes it), so the
    // pool's budget shrinks by one instead of showing the person as a slot.
    expect(screen.getByTestId('cover-ref-count').textContent).toContain('0 / 8');
  });
});


describe('CoverStudioOverlay — person upload and @-mentions', () => {
  it('a picture uploaded on the person card becomes the person', async () => {
    svc.importCanvasMedia.mockResolvedValueOnce({ kind: 'image', id: '321', url: '/api/v1/generated-media/321/cover' });
    renderOverlay();
    const file = new File(['x'], 'me.png', { type: 'image/png' });

    fireEvent.change(screen.getByTestId('cover-person-upload-input'), { target: { files: [file] } });

    await waitFor(() => expect(svc.importCanvasMedia).toHaveBeenCalledWith(file, null, null));
    await waitFor(() => expect(screen.getByTestId('cover-person-card').querySelector('img')).toBeTruthy());
    expect((screen.getByTestId('cover-generate') as HTMLButtonElement).disabled).toBe(false);
  });

  it('@{label} in the prompt is sent as a positional reference', async () => {
    renderOverlay();
    setPerson();
    await pickTemplate();
    fireEvent.change(screen.getByTestId('cover-instructions'), {
      target: { value: 'make it look like @{Bold headline} but with @{person} smiling' },
    });

    fireEvent.click(screen.getByTestId('cover-generate'));

    await waitFor(() =>
      expect(svc.generateCoverDrafts).toHaveBeenCalledWith(
        expect.objectContaining({
          instructions: 'make it look like reference image 2 (Bold headline) but with reference image 1 (person) smiling',
        }),
      ),
    );
  });

  it('picking from the library through "@" also mentions the picture', async () => {
    renderOverlay();
    fireEvent.click(screen.getByTestId('cover-mention-at'));
    fireEvent.click(await screen.findByTestId('cover-mention-library'));
    fireEvent.click(await screen.findByTestId('stub-template'));

    await waitFor(() =>
      expect((screen.getByTestId('cover-instructions') as HTMLTextAreaElement).value).toContain('@{Bold headline}'),
    );
  });
});


describe('CoverStudioOverlay — the prompt can be the topic', () => {
  it('unblocks without a publish title once the prompt box has words, and sends them as the topic', async () => {
    renderOverlay({ topic: '' });
    setPerson();
    expect((screen.getByTestId('cover-generate') as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByTestId('cover-instructions'), { target: { value: '仰望天空，文字是最伟大的作品' } });
    expect((screen.getByTestId('cover-generate') as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(screen.getByTestId('cover-generate'));
    await waitFor(() =>
      expect(svc.generateCoverDrafts).toHaveBeenCalledWith(expect.objectContaining({ topic: '仰望天空，文字是最伟大的作品' })),
    );
  });

  it('keeps the upload button on the person card after a person is set, and shortens long names', async () => {
    svc.importCanvasMedia.mockResolvedValueOnce({ kind: 'image', id: '321', url: '/api/v1/generated-media/321/cover' });
    renderOverlay();
    fireEvent.change(screen.getByTestId('cover-person-upload-input'), {
      target: { files: [new File(['x'], 'c284078e078abb510618eb422d9a1f2b3c4d5e6f.png', { type: 'image/png' })] },
    });
    await waitFor(() => expect(screen.getByTestId('cover-person-card').querySelector('img')).toBeTruthy());
    expect(screen.getByTestId('cover-person-upload')).toBeTruthy();
    expect(screen.getByTestId('cover-person-card').textContent).not.toContain('c284078e078abb510618eb422d9a1f2b3c4d5e6f');
  });
});


describe('CoverStudioOverlay — progress while drawing', () => {
  it('shows the engine percent and a running clock instead of a frozen label', async () => {
    let release: (v: unknown) => void = () => {};
    svc.awaitCoverGeneration.mockImplementationOnce((_id: string, opts: { onProgress?: (p: { phase: string; percent: number | null }) => void }) => {
      opts.onProgress?.({ phase: 'in_progress', percent: 40 });
      return new Promise((resolve) => { release = resolve; });
    });
    renderOverlay();
    setPerson();
    fireEvent.click(screen.getByTestId('cover-generate'));

    expect(await screen.findByTestId('cover-progress')).toBeTruthy();
    expect(screen.getByTestId('cover-progress-percent').textContent).toBe('40%');
    expect(screen.getByTestId('cover-progress-elapsed')).toBeTruthy();
    expect(screen.getByTestId('cover-generate').textContent).toContain('40%');

    release({ ok: true, url: '/api/v1/generated-media/500/cover', generatedMediaId: '500' });
    await waitFor(() => expect(screen.queryByTestId('cover-progress')).toBeNull());
  });
});


describe('CoverStudioOverlay — rounds survive closing the dialog', () => {
  it('rebuilds past rounds for this video from the server, finals attached to their grids', async () => {
    svc.listCoverRounds.mockResolvedValueOnce([
      { genId: '600', url: '/api/v1/generated-media/600/cover', stage: 2, aspect: '3:4', topic: 't', selectedDraft: 2, gridGenId: '500', createdAt: '2026-08-28T00:00:01Z' },
      { genId: '500', url: '/api/v1/generated-media/500/cover', stage: 1, aspect: '3:4', topic: 't', selectedDraft: null, gridGenId: null, createdAt: '2026-08-28T00:00:00Z' },
      { genId: '700', url: '/api/v1/generated-media/700/cover', stage: 1, aspect: '4:3', topic: 't', selectedDraft: null, gridGenId: null, createdAt: '2026-08-28T00:00:02Z' },
    ]);
    renderOverlay();

    await waitFor(() => expect(svc.listCoverRounds).toHaveBeenCalledWith('900'));
    // Vertical tab: one round (grid 500 with its final 600); the 4:3 round is under the other tab.
    expect(await screen.findByTestId('cover-history-round-1')).toBeTruthy();
    expect(screen.queryByTestId('cover-history-round-2')).toBeNull();
    expect(screen.getByTestId('cover-history-round-1').textContent).toContain('final');

    fireEvent.click(screen.getByTestId('cover-history-round-1'));
    expect(await screen.findByTestId('cover-final-image')).toBeTruthy();
    expect(svc.generateCoverDrafts).not.toHaveBeenCalled();
  });

  it('stamps the source video and the grid id on what it sends', async () => {
    renderOverlay();
    setPerson();
    fireEvent.click(screen.getByTestId('cover-generate'));
    await waitFor(() => expect(svc.generateCoverDrafts).toHaveBeenCalledWith(expect.objectContaining({ sourceVideoId: '900' })));
    await waitFor(() => expect(screen.queryByTestId('cover-draft-1')).toBeTruthy());
    fireEvent.click(screen.getByTestId('cover-draft-1'));
    svc.awaitCoverGeneration.mockResolvedValueOnce({ ok: true, url: '/api/v1/generated-media/600/cover', generatedMediaId: '600' });
    fireEvent.click(screen.getByTestId('cover-make-final'));
    await waitFor(() => expect(svc.refineCoverDraft).toHaveBeenCalledWith(expect.objectContaining({ gridGenId: '500', sourceVideoId: '900' })));
  });
});
