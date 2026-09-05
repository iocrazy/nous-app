import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

// A stand-in bundle rather than a bare `d ?? k`: the error path resolves
// `generated.err.<code>` with `generated.err.generic` AS its default, so a
// mock that always returns the default could not tell a correctly mapped
// code from an unmapped one.
const BUNDLE: Record<string, string> = {
  'generated.err.not_a_member': 'You are not a member of this workspace',
  'generated.err.generic': 'Something went wrong',
};
// Emulates i18next's two call forms — `t(key, 'Default')` and
// `t(key, { var, defaultValue })` — including interpolation. A mock that only
// understood the string form would silently drop every `{{var}}`.
const translate = (
  bundle: Record<string, string>,
  key: string,
  opts?: string | Record<string, unknown>,
): string => {
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
    t: (k: string, o?: string | Record<string, unknown>) => translate(BUNDLE, k, o),
  }),
}));

const addToast = vi.fn();
vi.mock('../../Toast', () => ({
  useToast: () => ({ addToast }),
}));

vi.mock('../../../services/generatedMediaService', () => ({
  generatedMediaCoverUrl: (id: string) => `https://api.test/gen/${id}/cover`,
  generatedMediaStreamUrl: (id: string) => `https://api.test/gen/${id}/stream`,
}));

const cleanupGenerated = vi.fn();
vi.mock('../../../services/generatedService', async () => {
  const { GeneratedApiError } = await import('../../../services/apiEnvelope');
  return {
    cleanupGenerated: (...a: unknown[]) => cleanupGenerated(...a),
    GeneratedApiError,
  };
});

import { CleanupDialog } from './CleanupDialog';
import { GeneratedApiError } from '../../../services/apiEnvelope';
import type { GeneratedItem } from '../../../services/generatedService';

const sampleItem = (id: string): GeneratedItem => ({
  id,
  scope_id: '727145299382534200',
  media_kind: 'image',
  mime: 'image/png',
  prompt: 'Prompt 0. more',
  model: 'gpt-image-2',
  provider: 'openai',
  origin_kind: 'canvas_run',
  canvas_id: '325005725244722',
  node_id: 'n9',
  created_at: '2026-08-28T10:00:00Z',
  promoted_resource_id: null,
  review_state: 'unreviewed',
  source_asset_id: '727145299382534201',
  source: {
    kind: 'canvas_run',
    label: 'EP1 · Storyboard · Canvas',
    canvas_id: '325005725244722',
    node_id: 'n9',
    shot_id: null,
    conversation_id: null,
    deep_link: '/team/727145299382534200/canvas/325005725244722?node=n9',
  },
  title: 'Prompt 0',
});

const dryRun = (over: Record<string, unknown> = {}) => ({
  dry_run: true,
  count: 3,
  sample: [sampleItem('727145299382534145')],
  deleted: 0,
  truncated: false,
  ...over,
});

const renderDialog = (props: Partial<React.ComponentProps<typeof CleanupDialog>> = {}) => {
  const onClose = vi.fn();
  const onDone = vi.fn();
  render(
    <CleanupDialog scopeId="727145299382534200" onClose={onClose} onDone={onDone} {...props} />,
  );
  return { onClose, onDone };
};

const daysInput = () => screen.getByLabelText(/Older Than/i) as HTMLInputElement;

beforeEach(() => {
  cleanupGenerated.mockReset();
  addToast.mockReset();
});

describe('CleanupDialog — preview gate', () => {
  it('defaults to 30 days, min 1, and offers no destructive button before a preview', () => {
    renderDialog();
    expect(daysInput().value).toBe('30');
    expect(daysInput().min).toBe('1');
    expect(screen.getByRole('button', { name: 'Preview' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Delete/ })).toBeNull();
  });

  it('previews with dry_run true, then deletes with dry_run false', async () => {
    cleanupGenerated.mockResolvedValueOnce(dryRun());
    const { onDone } = renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() => expect(cleanupGenerated).toHaveBeenCalledTimes(1));
    expect(cleanupGenerated).toHaveBeenLastCalledWith('727145299382534200', {
      older_than_days: 30,
      dry_run: true,
    });

    // The preview's own numbers reach the user before the confirm does.
    const confirm = await screen.findByRole('button', { name: 'Delete 3' });

    cleanupGenerated.mockResolvedValueOnce({ ...dryRun(), dry_run: false, deleted: 3 });
    fireEvent.click(confirm);

    await waitFor(() => expect(cleanupGenerated).toHaveBeenCalledTimes(2));
    expect(cleanupGenerated).toHaveBeenLastCalledWith('727145299382534200', {
      older_than_days: 30,
      dry_run: false,
    });
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(addToast).toHaveBeenCalledWith('3 deleted', 'success');
  });

  it('re-requires a preview once the day count changes', async () => {
    cleanupGenerated.mockResolvedValueOnce(dryRun());
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(await screen.findByRole('button', { name: 'Delete 3' })).toBeTruthy();

    fireEvent.change(daysInput(), { target: { value: '7' } });

    // The stale preview — and the confirm it authorised — are gone: a
    // "Delete 3" that now means a different 3 is the whole hazard here.
    expect(screen.queryByRole('button', { name: /^Delete/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Preview' })).toBeTruthy();

    cleanupGenerated.mockResolvedValueOnce(dryRun({ count: 9 }));
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() =>
      expect(cleanupGenerated).toHaveBeenLastCalledWith('727145299382534200', {
        older_than_days: 7,
        dry_run: true,
      }),
    );
    expect(await screen.findByRole('button', { name: 'Delete 9' })).toBeTruthy();
  });

  it('offers nothing to confirm when the preview matched nothing', async () => {
    cleanupGenerated.mockResolvedValueOnce(dryRun({ count: 0, sample: [] }));
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(await screen.findByText('Nothing To Clean Up')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Delete/ })).toBeNull();
  });
});

describe('CleanupDialog — preview contents', () => {
  it('shows at most 12 sample thumbs', async () => {
    const sample = Array.from({ length: 20 }, (_, i) => sampleItem(`72714529938253${4000 + i}`));
    cleanupGenerated.mockResolvedValueOnce(dryRun({ count: 20, sample }));
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() =>
      expect(document.querySelectorAll('[data-testid="cleanup-sample"]').length).toBe(12),
    );
  });

  // Same gap the grid card had: a `/cover` request for an mp3 or a docx draws
  // a broken-image icon, and in a delete preview that reads as "these rows are
  // already damaged" — the opposite of the reassurance the preview exists for.
  it('draws a placeholder, not an <img>, for a row with no preview', async () => {
    cleanupGenerated.mockResolvedValueOnce(
      dryRun({
        count: 2,
        sample: [
          { ...sampleItem('727145299382534145'), media_kind: 'audio', mime: 'audio/mpeg' },
          { ...sampleItem('727145299382534146'), media_kind: 'file', mime: 'application/pdf' },
        ],
      }),
    );
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() =>
      expect(document.querySelectorAll('[data-testid="cleanup-sample"]').length).toBe(2),
    );
    const tiles = Array.from(document.querySelectorAll('[data-testid="cleanup-sample"]'));
    expect(tiles.map((el) => el.getAttribute('data-media-kind'))).toEqual(['audio', 'file']);
    expect(tiles[0].querySelector('.lucide-audio-lines')).toBeTruthy();
    expect(tiles[1].querySelector('.lucide-file')).toBeTruthy();
    expect(document.querySelectorAll('[data-testid="cleanup-sample"] img').length).toBe(0);
    expect(document.querySelector('img[data-testid="cleanup-sample"]')).toBeNull();
  });

  it('still uses the cover thumbnail for an image or a video row', async () => {
    cleanupGenerated.mockResolvedValueOnce(
      dryRun({
        count: 2,
        sample: [
          sampleItem('727145299382534145'),
          { ...sampleItem('727145299382534146'), media_kind: 'video', mime: 'video/mp4' },
        ],
      }),
    );
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() =>
      expect(document.querySelectorAll('img[data-testid="cleanup-sample"]').length).toBe(2),
    );
  });

  it('says so when the pass was truncated', async () => {
    cleanupGenerated.mockResolvedValueOnce(dryRun({ count: 2000, truncated: true }));
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(await screen.findByText(/Showing the first 2000/)).toBeTruthy();
  });

  it('does not claim truncation when the pass was complete', async () => {
    cleanupGenerated.mockResolvedValueOnce(dryRun());
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await screen.findByRole('button', { name: 'Delete 3' });
    expect(screen.queryByText(/Showing the first 2000/)).toBeNull();
  });
});

describe('CleanupDialog — failures', () => {
  it('surfaces a typed refusal through the mapped i18n key', async () => {
    cleanupGenerated.mockRejectedValueOnce(
      new GeneratedApiError(403, 'not_a_member', 'You are not a member of this scope'),
    );
    const { onDone } = renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('You are not a member of this workspace', 'error'),
    );
    expect(onDone).not.toHaveBeenCalled();
    // Still previewable — a refusal must not leave a dead dialog.
    expect(screen.getByRole('button', { name: 'Preview' })).toBeTruthy();
  });
});
