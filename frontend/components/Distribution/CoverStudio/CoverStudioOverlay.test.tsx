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
}));

vi.mock('../../../services/coverStudioService', () => ({
  generateCoverDrafts: svc.generateCoverDrafts,
  refineCoverDraft: svc.refineCoverDraft,
  awaitCoverGeneration: svc.awaitCoverGeneration,
}));
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
  CoverFrameGrabber: ({ onGrabbed }: { onGrabbed: (f: unknown) => void }) => (
    <button
      type="button"
      data-testid="stub-grab"
      onClick={() =>
        onGrabbed({
          generatedMediaId: '111',
          url: '/api/v1/generated-media/111/cover',
          timestampSeconds: 3.1,
        })
      }
    >
      grab
    </button>
  ),
}));
vi.mock('./CoverTemplateGrid', () => ({
  CoverTemplateGrid: ({ onToggle }: { onToggle: (t: unknown) => void }) => (
    <button
      type="button"
      data-testid="stub-template"
      onClick={() =>
        onToggle({
          resource_id: 'tpl-7',
          name: 'Bold headline',
          mime_type: 'image/png',
          thumb_url: '/api/v1/resources/tpl-7/cover',
          usage_count: 0,
          last_used_at: null,
        })
      }
    >
      tpl
    </button>
  ),
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

/** Tick the person checkbox, then grab — the shortest path to "person set". */
function setPerson() {
  fireEvent.click(screen.getByTestId('cover-grab-as-person'));
  fireEvent.click(screen.getByTestId('stub-grab'));
}

beforeEach(() => {
  vi.clearAllMocks();
  svc.listGenerationModels.mockResolvedValue([]);
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
      /give the publish a title first/,
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

describe('CoverStudioOverlay — the person checkbox', () => {
  it('unticks itself after the person grab', () => {
    // Leaving it ticked would turn every next grab into a person swap the
    // user did not ask for.
    renderOverlay();
    setPerson();

    expect(
      (screen.getByTestId('cover-grab-as-person') as HTMLInputElement).checked,
    ).toBe(false);
  });
});

describe('CoverStudioOverlay — the two-stage run', () => {
  it('sends the pool with the person first, then shows the grid', async () => {
    renderOverlay();
    setPerson();
    fireEvent.click(screen.getByTestId('stub-template'));
    await screen.findByText('Bold headline');

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
    fireEvent.click(screen.getByTestId('stub-template'));
    await screen.findByText('Bold headline');
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
    await waitFor(() => expect(onApply).toHaveBeenCalledWith('9000'));
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
