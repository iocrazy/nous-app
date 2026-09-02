/**
 * AssetCard — read against REAL `AssetResponse` rows.
 *
 * The fixtures below are copied verbatim from
 * `.superpowers/sdd/2026-08-29-asset-library-p2-codex-and-sheets/wire-fixtures-assets.json`
 * (`GET /assets`), including the shapes it is tempting to "tidy": ids are
 * STRINGS, `scope_id` is null on a preset, `file_counts_by_slot` is sparse
 * (a slot with no files is ABSENT, not `0`), and `tags` is an object of
 * group → values rather than a flat array. A prettified fixture would let a
 * card that crashes on the real payload pass here.
 *
 * What the card must get right, and what each is worth:
 *   * `missing` is RENDERED. "Draft" alone tells a user they cannot use the
 *     asset without telling them what to do about it.
 *   * ring colour follows readiness (ok/warn), ring FRACTION follows slot
 *     coverage. They answer different questions; a card that drove both off
 *     one number would say a ready-but-bare character is complete.
 *   * a missing cover falls back to the type icon, not to a broken <img>.
 */
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

const translate = (key: string, opts?: string | Record<string, unknown>): string => {
  const bundle: Record<string, string> = {
    'saveAsAsset.slot.sheet': 'Sheet',
    'saveAsAsset.slot.stills': 'Stills',
    'saveAsAsset.type.character': 'Character',
    'saveAsAsset.type.prompt': 'Prompt',
    'assets.readiness.ready': 'Ready',
    'assets.readiness.draft': 'Draft',
    'assets.card.missing': 'Missing: {{slots}}',
    'assets.card.coverage': '{{filled}} of {{total}} slots filled',
    'assets.card.open': 'Open {{name}}',
    'assets.card.moreProjects': '+{{n}}',
  };
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
    t: (k: string, o?: string | Record<string, unknown>) => translate(k, o),
  }),
}));

vi.mock('../../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `https://api.test/resources/${id}/cover`,
}));

import { AssetCard, slotCoverage } from './AssetCard';
import type { AssetRow } from '../../../services/assetsService';

const READY_CHARACTER: AssetRow = {
  id: '727145299382534300',
  scope_id: '727145299382534200',
  asset_type: 'character',
  subtype: null,
  name: 'Sang Yao',
  role_tag: 'lead',
  description: 'Late twenties, wind-burnt.',
  attrs: {},
  prompt_positive: 'same woman as reference…',
  prompt_negative: null,
  prompt_positive_zh: null,
  prompt_negative_zh: null,
  platform_params: {},
  cover_file_id: '727145299382534146',
  source: 'manual',
  duplicated_from: null,
  is_system_preset: false,
  in_library: true,
  tags: { role: ['lead'] },
  sort_order: 0,
  created_by: '11111111-1111-1111-1111-111111111111',
  created_at: '2026-08-29T10:00:00Z',
  updated_at: '2026-08-29T10:00:00Z',
  readiness: { state: 'ready', missing: [] },
  file_counts_by_slot: { sheet: 1, stills: 5 },
  project_ids: ['55'],
  loadout_count: 2,
};

const DRAFT_CHARACTER: AssetRow = {
  ...READY_CHARACTER,
  id: '727145299382534301',
  name: 'Fan Qi',
  cover_file_id: null,
  prompt_positive: null,
  readiness: { state: 'draft', missing: ['sheet'] },
  file_counts_by_slot: { stills: 1 },
  loadout_count: 1,
};

const PRESET_PROMPT: AssetRow = {
  ...READY_CHARACTER,
  id: '727145299382534303',
  scope_id: null,
  asset_type: 'prompt',
  name: 'Multi-angle 3x3 sheet',
  role_tag: '',
  source: 'system_preset',
  is_system_preset: true,
  in_library: true,
  readiness: { state: 'ready', missing: [] },
  file_counts_by_slot: {},
  project_ids: [],
  loadout_count: 0,
};

const renderCard = (props: Partial<React.ComponentProps<typeof AssetCard>> = {}) =>
  render(<AssetCard asset={READY_CHARACTER} onOpen={() => {}} {...props} />);

describe('AssetCard — readiness', () => {
  it('a ready asset shows the Ready chip and an ok-toned ring', () => {
    renderCard();
    expect(screen.getByTestId('readiness-chip').textContent).toBe('Ready');
    expect(screen.getByTestId('readiness-ring').querySelectorAll('circle')[1].getAttribute('class'))
      .toContain('stroke-ok');
    expect(screen.getByTestId('asset-card').getAttribute('data-readiness')).toBe('ready');
  });

  // Split from the ring assertion on purpose: one test carrying two
  // properties can only ever fail for whichever breaks first, so neither can
  // be independently falsified (CLAUDE.md — "一个 assert 塞多个性质").
  it('a draft asset NAMES what is missing, not just that it is a draft', () => {
    renderCard({ asset: DRAFT_CHARACTER });
    const chip = screen.getByTestId('readiness-chip');
    expect(chip.textContent).toContain('Draft');
    // Translated through the shared slot namespace, so a shelf chip reads the
    // same as the slot picker: `sheet` → "Sheet".
    expect(chip.textContent).toContain('Missing: Sheet');
  });

  it('a draft asset draws a warn-toned ring', () => {
    renderCard({ asset: DRAFT_CHARACTER });
    expect(
      screen.getByTestId('readiness-ring').querySelectorAll('circle')[1].getAttribute('class'),
    ).toContain('stroke-warn');
  });
});

describe('AssetCard — slot coverage', () => {
  it('draws one square per slot in the type table, filled ones marked', () => {
    renderCard();
    const squares = within(screen.getByTestId('asset-card-slots')).getAllByTitle(/·/);
    // character: sheet, stills, expressions, extras, worn
    expect(squares).toHaveLength(5);
    const filled = squares
      .filter((el) => el.getAttribute('data-slot-filled') === 'true')
      .map((el) => el.getAttribute('data-slot'));
    expect(filled).toEqual(['sheet', 'stills']);
  });

  it('an ABSENT slot key counts as empty, not as a crash', () => {
    // `file_counts_by_slot` is sparse on the wire; `expressions` simply is
    // not there. Reading it as `undefined > 0` must be false, not NaN.
    renderCard();
    const square = screen.getByTitle('expressions · 0');
    expect(square.getAttribute('data-slot-filled')).toBe('false');
  });

  it('the ring reports coverage, which is NOT the readiness boolean', () => {
    renderCard();
    const ring = screen.getByTestId('readiness-ring');
    expect(ring.getAttribute('data-ring-filled')).toBe('2');
    expect(ring.getAttribute('data-ring-total')).toBe('5');
    // A ready asset with 2 of 5 slots: ok colour, partial ring.
    expect(ring.getAttribute('aria-label')).toBe('2 of 5 slots filled');
  });

  it('a prompt counts its own body, so a ready preset is not an empty ring', () => {
    // `PRIMARY_SLOT.prompt` is null — a prompt is ready because its BODY has
    // text, not because a file landed. Counting only file slots would draw an
    // empty ok-coloured ring, which reads as a contradiction.
    expect(slotCoverage(PRESET_PROMPT)).toEqual({ filled: 1, total: 2 });
    expect(slotCoverage(READY_CHARACTER)).toEqual({ filled: 2, total: 5 });
    expect(slotCoverage(DRAFT_CHARACTER)).toEqual({ filled: 1, total: 5 });
  });
});

describe('AssetCard — portrait, chips and navigation', () => {
  it('uses the cover route when the asset has a cover file', () => {
    renderCard();
    expect(screen.getByRole('img', { name: 'Sang Yao' }).getAttribute('src')).toBe(
      'https://api.test/resources/727145299382534146/cover',
    );
  });

  it('falls back to the type icon rather than a broken image', () => {
    renderCard({ asset: DRAFT_CHARACTER });
    expect(screen.queryByRole('img', { name: 'Fan Qi' })).toBeNull();
    // The lucide icon is an <svg>; the readiness ring is the only other one,
    // so a second svg is the fallback having rendered.
    expect(screen.getByTestId('asset-card').querySelectorAll('svg').length).toBeGreaterThan(1);
  });

  it('shows the type tag only in the All view', () => {
    const { unmount } = renderCard({ showTypeTag: true });
    expect(screen.getByTestId('asset-card-type-tag').textContent).toBe('Character');
    unmount();
    renderCard();
    expect(screen.queryByTestId('asset-card-type-tag')).toBeNull();
  });

  it('names projects when it can, and never shows a raw id when it cannot', () => {
    const { unmount } = renderCard({ projectNames: { '55': 'Bamboo Reel' } });
    const named = screen.getByTestId('asset-card-project');
    expect(named.textContent).toBe('Bamboo Reel');
    // The tooltip carries the full NAME here: the chip truncates at 7rem, so
    // what it hides is the rest of the name, not the id.
    expect(named.getAttribute('title')).toBe('Bamboo Reel');
    unmount();

    // An unnamed project is still a project this asset is used in — dropping
    // the chip would under-report where the asset appears. But a raw
    // Snowflake is not a LABEL: it renders abbreviated, with the full id in
    // the tooltip.
    renderCard();
    const unnamed = screen.getByTestId('asset-card-project');
    expect(unnamed.textContent).toBe('#55');
    expect(unnamed.getAttribute('title')).toBe('55');
  });

  it('abbreviates a real Snowflake id rather than rendering all 18 digits', () => {
    // The bug this replaces: a project chip on a card fetched without a name
    // map showed the full BIGINT, wide enough to crowd out the chip beside it
    // and meaningless to read.
    const snowflake = '727145299382534055';
    renderCard({ asset: { ...READY_CHARACTER, project_ids: [snowflake] } });
    const chip = screen.getByTestId('asset-card-project');
    expect(chip.textContent).toBe('#…4055');
    expect(chip.textContent).not.toContain(snowflake);
    expect(chip.getAttribute('title')).toBe(snowflake);
  });

  it('marks an asset that is not in the library, and leaves members unmarked', () => {
    // Only the "out" state gets a badge: on the shelf, membership is the norm
    // and a badge on every card would be noise. On the project panel — where
    // both states sit side by side — this is what says which cards the Add To
    // Library action still applies to.
    const { unmount } = renderCard({
      asset: { ...READY_CHARACTER, in_library: false },
    });
    expect(screen.getByTestId('not-in-library-badge').textContent).toBe('Not In Library');
    unmount();
    renderCard();
    expect(screen.queryByTestId('not-in-library-badge')).toBeNull();
  });

  it('collapses the tail of a long project list into a count', () => {
    renderCard({ asset: { ...READY_CHARACTER, project_ids: ['55', '56', '57', '58'] } });
    expect(screen.getAllByTestId('asset-card-project')).toHaveLength(2);
    expect(screen.getByText('+2')).toBeTruthy();
  });

  it('omits the role line when the asset has none', () => {
    // `role_tag` defaults to "" server-side; an empty line would leave a gap
    // that reads as a missing value rather than an absent one.
    renderCard({ asset: PRESET_PROMPT });
    expect(screen.queryByText('lead')).toBeNull();
  });

  it('hands the whole row back on click', () => {
    const onOpen = vi.fn();
    renderCard({ onOpen });
    fireEvent.click(screen.getByTestId('asset-card'));
    expect(onOpen).toHaveBeenCalledWith(READY_CHARACTER);
  });
});
