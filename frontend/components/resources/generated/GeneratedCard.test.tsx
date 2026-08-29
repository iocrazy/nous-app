import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

const navigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate,
}));

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
  useTranslation: () => ({ t: (k: string, o?: string | Record<string, unknown>) => translate({}, k, o) }),
}));

vi.mock('../../../services/generatedMediaService', () => ({
  generatedMediaCoverUrl: (id: string) => `https://api.test/gen/${id}/cover`,
  generatedMediaStreamUrl: (id: string) => `https://api.test/gen/${id}/stream`,
}));

import { GeneratedCard } from './GeneratedCard';
import type { GeneratedItem } from '../../../services/generatedService';

// Copied from `.superpowers/sdd/.../wire-fixtures.json` — string ids, the
// exact shape `GET /generated` returns.
const UNREVIEWED: GeneratedItem = {
  id: '727145299382534145',
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
};

const SAVED: GeneratedItem = {
  ...UNREVIEWED,
  id: '727145299382534146',
  origin_kind: 'chat_upload',
  canvas_id: null,
  node_id: null,
  promoted_resource_id: '727145299382534301',
  review_state: 'saved',
  source_asset_id: null,
  source: {
    kind: 'chat_upload',
    label: 'Chat upload',
    canvas_id: null,
    node_id: null,
    shot_id: null,
    conversation_id: null,
    deep_link: null,
  },
  title: 'Prompt 1',
};

const IN_ASSETS: GeneratedItem = {
  ...SAVED,
  id: '727145299382534147',
  origin_kind: 'shot_generate',
  review_state: 'in_assets',
  source_asset_id: '727145299382534201',
  source: { ...SAVED.source, kind: 'shot_generate', label: 'Storyboard · Shot 7012' },
  title: 'Prompt 2',
};

const handlers = () => ({
  onSave: vi.fn(),
  onSaveAsAsset: vi.fn(),
  onDelete: vi.fn(),
  onToggleSelect: vi.fn(),
});

beforeEach(() => {
  navigate.mockReset();
});

describe('GeneratedCard — per-state affordances', () => {
  it('renders the New label and the three unreviewed actions', () => {
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...handlers()} />);

    expect(screen.getByText('New')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'As Asset…' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeTruthy();
  });

  it('renders the Saved label with a disabled "In My Uploads" and no delete', () => {
    render(<GeneratedCard item={SAVED} selected={false} teamId="t1" {...handlers()} />);

    expect(screen.getByText('Saved')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'In My Uploads' })).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: 'As Asset…' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });

  it('renders the Asset label with only "Open asset"', () => {
    render(<GeneratedCard item={IN_ASSETS} selected={false} teamId="t1" {...handlers()} />);

    expect(screen.getByText('Asset')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Open asset' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'As Asset…' })).toBeNull();
  });

  it('navigates to the asset route (P2) with the source asset id', () => {
    render(<GeneratedCard item={IN_ASSETS} selected={false} teamId="t1" {...handlers()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Open asset' }));
    expect(navigate).toHaveBeenCalledWith('/team/t1/resources/assets/727145299382534201');
  });

  it('calls onSave / onSaveAsAsset with the item', () => {
    const h = handlers();
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...h} />);

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(h.onSave).toHaveBeenCalledWith(UNREVIEWED);

    fireEvent.click(screen.getByRole('button', { name: 'As Asset…' }));
    expect(h.onSaveAsAsset).toHaveBeenCalledWith(UNREVIEWED);
  });
});

describe('GeneratedCard — source line', () => {
  it('is a button that deep-links, with the node hint as its tooltip', () => {
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...handlers()} />);

    const source = screen.getByRole('button', { name: /EP1 · Storyboard · Canvas/ });
    expect(source.getAttribute('title')).toBe('Opens the canvas at this node');

    fireEvent.click(source);
    expect(navigate).toHaveBeenCalledWith(
      '/team/727145299382534200/canvas/325005725244722?node=n9',
    );
  });

  it('is plain text when the backend supplied no deep link', () => {
    render(<GeneratedCard item={SAVED} selected={false} teamId="t1" {...handlers()} />);

    expect(screen.queryByRole('button', { name: /Chat upload/ })).toBeNull();
    expect(screen.getByText('Chat upload')).toBeTruthy();
  });
});

describe('GeneratedCard — delete confirmation', () => {
  it('requires a second click before onDelete fires', () => {
    const h = handlers();
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...h} />);

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(h.onDelete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Confirm Delete' }));
    expect(h.onDelete).toHaveBeenCalledWith(UNREVIEWED);
  });

  it('can be backed out of', () => {
    const h = handlers();
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...h} />);

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(h.onDelete).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Save' })).toBeTruthy();
  });
});

describe('GeneratedCard — selection and thumbnail', () => {
  it('reports checkbox toggles by id', () => {
    const h = handlers();
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...h} />);

    const box = screen.getByRole('checkbox', { name: /Select/ });
    expect(box).toHaveProperty('checked', false);
    fireEvent.click(box);
    expect(h.onToggleSelect).toHaveBeenCalledWith('727145299382534145');
  });

  it('reflects the selected prop', () => {
    render(<GeneratedCard item={UNREVIEWED} selected teamId="t1" {...handlers()} />);
    expect(screen.getByRole('checkbox', { name: /Select/ })).toHaveProperty('checked', true);
  });

  it('uses the cover URL for an image', () => {
    const { container } = render(
      <GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...handlers()} />,
    );
    const img = container.querySelector('img');
    expect(img?.getAttribute('src')).toBe('https://api.test/gen/727145299382534145/cover');
    expect(container.querySelector('video')).toBeNull();
  });

  it('uses a muted, non-autoplaying <video> with a cover poster for a video', () => {
    const { container } = render(
      <GeneratedCard
        item={{ ...UNREVIEWED, media_kind: 'video' }}
        selected={false}
        teamId="t1"
        {...handlers()}
      />,
    );
    const video = container.querySelector('video');
    expect(video?.getAttribute('poster')).toBe('https://api.test/gen/727145299382534145/cover');
    expect(video?.getAttribute('src')).toBe('https://api.test/gen/727145299382534145/stream');
    expect(video).toHaveProperty('muted', true);
    expect(video?.hasAttribute('autoplay')).toBe(false);
  });
});
