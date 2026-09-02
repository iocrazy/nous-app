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
    // Icon buttons: the accessible name is the ONLY thing naming them, so
    // this is not a formality — an icon without it is an unlabelled control.
    expect(screen.getByRole('button', { name: 'Save To Uploads' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add To Asset' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeTruthy();
  });

  it('renders the Saved label with a disabled "In My Uploads" and no delete', () => {
    render(<GeneratedCard item={SAVED} selected={false} teamId="t1" {...handlers()} />);

    expect(screen.getByText('Saved')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'In My Uploads' })).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: 'Add To Asset' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save To Uploads' })).toBeNull();
  });

  it('renders the Asset label with only "Open asset"', () => {
    render(<GeneratedCard item={IN_ASSETS} selected={false} teamId="t1" {...handlers()} />);

    expect(screen.getByText('Asset')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Open asset' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Save To Uploads' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Add To Asset' })).toBeNull();
  });

  it('navigates to the asset ITEM route (P2) with the source asset id', () => {
    // The `item/` segment is load-bearing. Until P2 shipped, this pointed at
    // `/resources/assets/{id}`, which matches the TYPE route
    // (`resources/assets/:assetType`) — a snowflake is not one of the six
    // slugs, so `AssetsView` redirected to the shelf and "Open asset" quietly
    // opened the whole library instead of the asset. This assertion was
    // written against that URL and therefore pinned the bug rather than the
    // behaviour its own name describes.
    render(<GeneratedCard item={IN_ASSETS} selected={false} teamId="t1" {...handlers()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Open asset' }));
    expect(navigate).toHaveBeenCalledWith(
      '/team/t1/resources/assets/item/727145299382534201',
    );
  });

  it('falls back to the asset index when the row has no source asset id', () => {
    // A row promoted before the asset link existed. Landing on the index is
    // the deliberate answer; what must NOT happen is a URL ending in a bare
    // `item/` or the string "null".
    const orphan = { ...IN_ASSETS, source_asset_id: null };
    render(<GeneratedCard item={orphan} selected={false} teamId="t1" {...handlers()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Open asset' }));
    expect(navigate).toHaveBeenCalledWith('/team/t1/resources/assets');
  });

  it('calls onSave / onSaveAsAsset with the item', () => {
    const h = handlers();
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...h} />);

    fireEvent.click(screen.getByRole('button', { name: 'Save To Uploads' }));
    expect(h.onSave).toHaveBeenCalledWith(UNREVIEWED);

    fireEvent.click(screen.getByRole('button', { name: 'Add To Asset' }));
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
    expect(screen.getByRole('button', { name: 'Save To Uploads' })).toBeTruthy();
  });
});

describe('GeneratedCard — selection and thumbnail', () => {
  it('reports checkbox toggles by id', () => {
    const h = handlers();
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...h} />);

    const box = screen.getByRole('checkbox', { name: /Select/ });
    expect(box.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(box);
    expect(h.onToggleSelect).toHaveBeenCalledWith('727145299382534145');
  });

  it('reflects the selected prop', () => {
    render(<GeneratedCard item={UNREVIEWED} selected teamId="t1" {...handlers()} />);
    expect(screen.getByRole('checkbox', { name: /Select/ }).getAttribute('aria-checked')).toBe(
      'true',
    );
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


describe('GeneratedCard — selection control mirrors My Uploads', () => {
  it('sits at the TOP-LEFT of the thumbnail and is a circle', () => {
    // The whole point of the change: the old control was a SQUARE at the
    // top-RIGHT, which is nowhere near what a My Uploads card does. Asserting
    // the position classes is the only way a regression here is caught —
    // "a checkbox exists" was already true of the wrong one.
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...handlers()} />);

    const box = screen.getByRole('checkbox', { name: /Select/ });
    expect(box.className).toContain('rounded-full');
    const wrapper = box.parentElement as HTMLElement;
    expect(wrapper.className).toContain('top-2');
    expect(wrapper.className).toContain('left-2');
    expect(wrapper.className).not.toContain('right-');
  });

  it('is in the DOM even when unselected and unhovered', () => {
    // A control that only mounts on hover is unreachable by keyboard and
    // invisible to every test that would guard it.
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...handlers()} />);
    expect(screen.getByRole('checkbox', { name: /Select/ })).toBeTruthy();
  });

  it('does not open the lightbox when it is clicked', () => {
    // It sits ON the thumbnail, which is now a button. Without the
    // stopPropagation, picking a card for a batch action would also throw the
    // viewer open over it.
    const h = handlers();
    const onOpen = vi.fn();
    render(
      <GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" onOpen={onOpen} {...h} />,
    );

    fireEvent.click(screen.getByRole('checkbox', { name: /Select/ }));
    expect(h.onToggleSelect).toHaveBeenCalled();
    expect(onOpen).not.toHaveBeenCalled();
  });
});

describe('GeneratedCard — icon actions carry both a tooltip and a label', () => {
  it('titles say what each action DOES, not just what it is called', () => {
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...handlers()} />);

    expect(
      screen.getByRole('button', { name: 'Save To Uploads' }).getAttribute('title'),
    ).toBe('Turn this into a regular file in My Uploads');
    expect(screen.getByRole('button', { name: 'Add To Asset' }).getAttribute('title')).toBe(
      "Attach it to an asset card's slot (character, location, …)",
    );
  });

  it('renders an icon rather than a word', () => {
    // If the button ever regains a text node, the name assertions above stop
    // proving anything about an ICON button.
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...handlers()} />);
    const save = screen.getByRole('button', { name: 'Save To Uploads' });
    expect(save.textContent).toBe('');
    expect(save.querySelector('svg')).toBeTruthy();
  });
});

describe('GeneratedCard — thumbnail opens the viewer', () => {
  it('calls onOpen with the item', () => {
    const onOpen = vi.fn();
    render(
      <GeneratedCard
        item={UNREVIEWED}
        selected={false}
        teamId="t1"
        onOpen={onOpen}
        {...handlers()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Preview Prompt 0/ }));
    expect(onOpen).toHaveBeenCalledWith(UNREVIEWED);
  });

  it('is disabled — not silently inert — when no handler was given', () => {
    // A clickable-looking thumbnail that does nothing is the failure mode
    // this replaces; saying so in the DOM is the honest version.
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...handlers()} />);
    expect(screen.getByRole('button', { name: /Preview Prompt 0/ })).toHaveProperty(
      'disabled',
      true,
    );
  });

  it('keeps the source deep-link button working alongside it', () => {
    const onOpen = vi.fn();
    render(
      <GeneratedCard
        item={UNREVIEWED}
        selected={false}
        teamId="t1"
        onOpen={onOpen}
        {...handlers()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /EP1 · Storyboard · Canvas/ }));
    expect(navigate).toHaveBeenCalledWith(
      '/team/727145299382534200/canvas/325005725244722?node=n9',
    );
    expect(onOpen).not.toHaveBeenCalled();
  });
});
