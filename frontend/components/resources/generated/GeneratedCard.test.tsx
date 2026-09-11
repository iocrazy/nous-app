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
    // Null on every non-`agent_run` row: `describe_source` only reads the
    // run_deliverables provenance in that one arm.
    issue_id: null,
    run_id: null,
    step: null,
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
    issue_id: null,
    run_id: null,
    step: null,
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
    expect(screen.getByRole('button', { name: 'As Asset' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeTruthy();
  });

  it('renders the Saved label with a disabled "In My Uploads" and no delete', () => {
    render(<GeneratedCard item={SAVED} selected={false} teamId="t1" {...handlers()} />);

    expect(screen.getByText('Saved')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'In My Uploads' })).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: 'As Asset' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save To Uploads' })).toBeNull();
  });

  it('renders the Asset label with only "Open asset"', () => {
    render(<GeneratedCard item={IN_ASSETS} selected={false} teamId="t1" {...handlers()} />);

    expect(screen.getByText('Asset')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Open asset' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Save To Uploads' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'As Asset' })).toBeNull();
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

    fireEvent.click(screen.getByRole('button', { name: 'As Asset' }));
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

  /**
   * `agent_run` rows (harness 3a Task 3 + 6). The backend now looks the row up
   * in `run_deliverables` and fills `issue_id / run_id / step / deep_link`, so
   * "which run made this picture?" is answerable from the inbox. Wire shape
   * copied from `describe_source`: ids are STRINGS, `step` is a number, and
   * `deep_link` is null whenever the provenance lookup found nothing.
   */
  const AGENT_RUN: GeneratedItem = {
    ...UNREVIEWED,
    source: {
      kind: 'agent_run',
      label: 'Script AI · MH-91',
      canvas_id: null,
      node_id: null,
      shot_id: null,
      conversation_id: null,
      deep_link: '/team/727145299382534200/todolist/MH-91?step=4',
      issue_id: '727145299382534000',
      run_id: '727145299382534100',
      step: 4,
    },
  };

  it('links an agent run to the issue that produced it', () => {
    render(<GeneratedCard item={AGENT_RUN} selected={false} teamId="t1" {...handlers()} />);
    fireEvent.click(screen.getByRole('button', { name: /Script AI · MH-91/ }));
    expect(navigate).toHaveBeenCalledWith('/team/727145299382534200/todolist/MH-91?step=4');
  });

  it('says the link opens an ISSUE, not "where this was generated"', () => {
    // One hint for two destinations would tell the reader a canvas opens when
    // an issue does. The canvas hint is keyed off `node_id`, which an
    // agent_run row does not have, so without its own arm this row would
    // inherit the vague fallback.
    render(<GeneratedCard item={AGENT_RUN} selected={false} teamId="t1" {...handlers()} />);
    const source = screen.getByRole('button', { name: /Script AI · MH-91/ });
    expect(source.getAttribute('title')).toBe('Opens the issue whose run produced this');
  });

  it('stays plain text for a historical agent_run with no provenance', () => {
    // Every row generated before the registry existed has `deep_link: null`
    // and null ids. That is normal history, not a fault — and a button that
    // navigated nowhere would be worse than a label.
    const historical: GeneratedItem = {
      ...AGENT_RUN,
      source: {
        ...AGENT_RUN.source,
        label: 'Chat generation',
        deep_link: null,
        issue_id: null,
        run_id: null,
        step: null,
      },
    };
    render(<GeneratedCard item={historical} selected={false} teamId="t1" {...handlers()} />);
    expect(screen.queryByRole('button', { name: /Chat generation/ })).toBeNull();
    expect(screen.getByText('Chat generation')).toBeTruthy();
  });

  /**
   * 3a §5 wants the source line to read "谁 · issue · run #… · 第几步". The
   * backend has sent `run_id` / `step` since Task 3, and the card printed
   * neither: two cards from two different runs of the same issue were the
   * same four words, and "which run made THIS one?" was still unanswerable
   * without opening the issue.
   */
  it('names the run and the step after the label', () => {
    render(<GeneratedCard item={AGENT_RUN} selected={false} teamId="t1" {...handlers()} />);
    const source = screen.getByRole('button', { name: /Script AI · MH-91/ });
    expect(source.textContent).toBe('Script AI · MH-91 · run #534100 · step 4');
  });

  it('prints step 0 — steps are 0-based, and the first one is not "no step"', () => {
    const first: GeneratedItem = { ...AGENT_RUN, source: { ...AGENT_RUN.source, step: 0 } };
    render(<GeneratedCard item={first} selected={false} teamId="t1" {...handlers()} />);
    expect(screen.getByRole('button', { name: /Script AI/ }).textContent).toContain('step 0');
  });

  it('a row with no run provenance keeps the bare label', () => {
    // Canvas rows, historical rows: nothing to append, and a trailing
    // separator would read as a truncated line.
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...handlers()} />);
    const source = screen.getByRole('button', { name: /EP1 · Storyboard · Canvas/ });
    expect(source.textContent).toBe('EP1 · Storyboard · Canvas');
  });

  it('never shows a raw snowflake in the source line', () => {
    // `issue_id` and `run_id` are provenance for the LINK, not words for a
    // reader. The label is the only thing that gets printed.
    render(<GeneratedCard item={AGENT_RUN} selected={false} teamId="t1" {...handlers()} />);
    expect(screen.queryByText(/727145299382534000/)).toBeNull();
    expect(screen.queryByText(/727145299382534100/)).toBeNull();
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

  // `audio` reaches the inbox from P6's My Uploads → As Asset, `file` from a
  // chat upload that is neither image nor video. Both used to fall through to
  // the `<img>` above and render a broken-image icon; the assertion that
  // matters is therefore "no <img>", not just "an icon exists".
  it('draws an audio placeholder — an icon and the format, never an <img>', () => {
    const { container } = render(
      <GeneratedCard
        item={{ ...UNREVIEWED, media_kind: 'audio', mime: 'audio/mpeg' }}
        selected={false}
        teamId="t1"
        {...handlers()}
      />,
    );

    const placeholder = screen.getByTestId('generated-card-placeholder');
    expect(placeholder.getAttribute('data-media-kind')).toBe('audio');
    expect(placeholder.querySelector('.lucide-audio-lines')).toBeTruthy();
    expect(placeholder.textContent).toBe('MPEG');
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('video')).toBeNull();
    // The row's name still comes from the title line under the tile — the
    // wire carries no filename of its own.
    expect(screen.getByText('Prompt 0')).toBeTruthy();
  });

  it('draws a generic file placeholder for a `file` row', () => {
    const { container } = render(
      <GeneratedCard
        item={{ ...UNREVIEWED, media_kind: 'file', mime: 'application/pdf' }}
        selected={false}
        teamId="t1"
        {...handlers()}
      />,
    );

    const placeholder = screen.getByTestId('generated-card-placeholder');
    expect(placeholder.getAttribute('data-media-kind')).toBe('file');
    expect(placeholder.querySelector('.lucide-file')).toBeTruthy();
    expect(placeholder.textContent).toBe('PDF');
    expect(container.querySelector('img')).toBeNull();
  });

  it('names the kind when the row carries no mime to badge', () => {
    render(
      <GeneratedCard
        item={{ ...UNREVIEWED, media_kind: 'audio', mime: null }}
        selected={false}
        teamId="t1"
        {...handlers()}
      />,
    );

    expect(screen.getByTestId('generated-card-placeholder').textContent).toBe('Audio');
  });

  it('keeps every action a placeholder row is entitled to', () => {
    const h = handlers();
    render(
      <GeneratedCard
        item={{ ...UNREVIEWED, media_kind: 'audio', mime: 'audio/mpeg' }}
        selected={false}
        teamId="t1"
        onOpen={vi.fn()}
        {...h}
      />,
    );

    expect(screen.getByRole('button', { name: 'Save To Uploads' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'As Asset' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: /Select/ }));
    expect(h.onToggleSelect).toHaveBeenCalledWith(UNREVIEWED.id);
  });

  // An unrecognised kind stays on the image path on purpose: the backend's own
  // `media_kind_from_mime` defaults an unknown mime to `image`, so showing a
  // file icon here would contradict what the writer decided.
  it('leaves an unknown kind on the image path', () => {
    const { container } = render(
      <GeneratedCard
        item={{ ...UNREVIEWED, media_kind: 'hologram' }}
        selected={false}
        teamId="t1"
        {...handlers()}
      />,
    );

    expect(container.querySelector('img')).toBeTruthy();
    expect(screen.queryByTestId('generated-card-placeholder')).toBeNull();
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
  it('titles carry the NAME and then what the action does', () => {
    // Both halves are asserted. The name half is what puts the new copy in
    // front of a sighted mouse user — with the hint alone, "Save To Uploads"
    // appeared only in the batch bar and the lightbox. The hint half is what
    // makes an icon legible at all.
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...handlers()} />);

    expect(
      screen.getByRole('button', { name: 'Save To Uploads' }).getAttribute('title'),
    ).toBe('Save To Uploads — Turn this into a regular file in My Uploads');
    expect(screen.getByRole('button', { name: 'As Asset' }).getAttribute('title')).toBe(
      "As Asset — Attach it to an asset card's slot (character, location, …)",
    );
  });

  it('announces the SHORT name, not the whole explanation', () => {
    // The split is the point: `aria-label` stays short so a screen reader
    // walking a grid of 40 cards does not read the hint forty times.
    render(<GeneratedCard item={UNREVIEWED} selected={false} teamId="t1" {...handlers()} />);

    const save = screen.getByRole('button', { name: 'Save To Uploads' });
    expect(save.getAttribute('aria-label')).toBe('Save To Uploads');
    expect(save.getAttribute('aria-label')).not.toContain('My Uploads folder');
    expect(screen.getByRole('button', { name: 'Delete' }).getAttribute('title')).toBe('Delete');
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
