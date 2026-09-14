/**
 * CompactMediaCard's adaptive-view additions (My Downloads).
 *
 * The card is shared between the uniform grid and the adaptive rows, so the
 * fixed 2:3 tile must survive untouched when no ratio is supplied — that is
 * what keeps "grid mode is unchanged" true.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import React from 'react'

vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ session: null }) }))
vi.mock('../services/resourceService', () => ({ getPreviewSpriteUrl: () => null }))
vi.mock('../utils/coverSource', () => ({ pickCoverFrame: () => null }))

import { CompactMediaCard } from './CompactMediaCard'
import type { Video } from '../types'

const item = {
  id: '1',
  platform_id: 'p1',
  original_url: 'https://example.com/x',
  title: 'Test Clip',
  media_type: 'video',
  cover_download_path: '/covers/p1.jpg',
  cover_download_status: 'completed',
} as unknown as Video

/** The thumbnail tile is the element carrying the aspect class or style. */
function tileOf(container: HTMLElement): HTMLElement {
  const el = container.querySelector('.bg-black.cursor-pointer')
  if (!el) throw new Error('thumbnail tile not found')
  return el as HTMLElement
}

describe('CompactMediaCard — adaptive tile', () => {
  it('keeps the fixed 2:3 tile when no aspect ratio is supplied', () => {
    const { container } = render(<CompactMediaCard data={item} onClick={() => {}} />)
    const tile = tileOf(container)
    expect(tile.className).toContain('aspect-[2/3]')
    expect(tile.style.aspectRatio).toBe('')
  })

  it('renders at the supplied ratio instead of the fixed tile', () => {
    const { container } = render(
      <CompactMediaCard data={item} onClick={() => {}} aspectRatio={1.5} />,
    )
    const tile = tileOf(container)
    expect(tile.className).not.toContain('aspect-[2/3]')
    // The DOM normalises a bare number to the `<w> / <h>` form.
    expect(tile.style.aspectRatio).toBe('1.5 / 1')
  })

  it('reports the cover\'s natural ratio once it loads', () => {
    const onThumbnailAspect = vi.fn()
    const { container } = render(
      <CompactMediaCard
        data={item}
        onClick={() => {}}
        aspectRatio={1.5}
        onThumbnailAspect={onThumbnailAspect}
      />,
    )
    const img = container.querySelector('img')
    expect(img).not.toBeNull()

    Object.defineProperty(img!, 'naturalWidth', { value: 1200, configurable: true })
    Object.defineProperty(img!, 'naturalHeight', { value: 800, configurable: true })
    fireEvent.load(img!)

    expect(onThumbnailAspect).toHaveBeenCalledWith(1.5)
  })

  it('reports nothing when the cover has no intrinsic size', () => {
    // A broken or still-decoding image reports 0x0; feeding that to the layout
    // would produce a NaN row height.
    const onThumbnailAspect = vi.fn()
    const { container } = render(
      <CompactMediaCard
        data={item}
        onClick={() => {}}
        aspectRatio={1.5}
        onThumbnailAspect={onThumbnailAspect}
      />,
    )
    const img = container.querySelector('img')!
    Object.defineProperty(img, 'naturalWidth', { value: 0, configurable: true })
    Object.defineProperty(img, 'naturalHeight', { value: 0, configurable: true })
    fireEvent.load(img)

    expect(onThumbnailAspect).not.toHaveBeenCalled()
  })

  it('does not throw when the load fires with no reporter attached', () => {
    const { container } = render(
      <CompactMediaCard data={item} onClick={() => {}} aspectRatio={1.5} />,
    )
    const img = container.querySelector('img')!
    expect(() => fireEvent.load(img)).not.toThrow()
  })
})

/**
 * The engagement-stats row.
 *
 * Two earlier shapes each broke one of the two constraints, which is why both
 * are pinned here:
 *
 *  * FOUR COLOURED BOXES needed ~34px each, so on a portrait card in the
 *    adaptive rows the numbers overflowed their boxes and read as one smear.
 *  * THE SAME FOUR WRAPPED 2×2 fixed the digits and broke the layout: only the
 *    narrow cards wrapped, so they hung below their row's baseline. A card in
 *    a justified row must end where its neighbours end.
 *
 * So the row must be ONE LINE at every width, and it must never print a number
 * it cannot fit. jsdom resolves no container queries, so what these can pin is
 * the contract that produces that: a single flex row (no grid, no wrap), likes
 * always present, the rest gated on measured thresholds, and every number
 * still reachable through the row's title.
 */
describe('CompactMediaCard — engagement stats', () => {
  const withCounts = {
    ...item,
    like_count: 1634,
    comment_count: 160,
    share_count: 41,
    favorite_count: 427,
    original_url: 'https://example.com/x',
  } as unknown as Video

  /** The stats row: the flex row holding the four metrics. */
  function statsRow(container: HTMLElement): HTMLElement {
    const el = container.querySelector('[title*="♥"]')
    if (!el) throw new Error('stats row not found')
    return el as HTMLElement
  }

  it('is one flex row, never a grid — its height must not follow its width', () => {
    const { container } = render(<CompactMediaCard data={withCounts} onClick={() => {}} />)
    const row = statsRow(container)
    expect(row.className).toContain('flex')
    expect(row.className).not.toContain('grid')
    expect(row.className).not.toContain('flex-wrap')
  })

  it('always shows likes — the one metric that survives every width', () => {
    const { container } = render(<CompactMediaCard data={withCounts} onClick={() => {}} />)
    const first = statsRow(container).children[0] as HTMLElement
    expect(first.className).not.toContain('hidden')
    expect(first.textContent).toContain('1.6K')
  })

  // Thresholds measured against the WIDEST number `formatNumber` can emit
  // (`999.9K`), not a typical one — a first pass used `1.6K` and the extremes
  // then overflowed.
  it('gates the other three on ascending measured thresholds', () => {
    const { container } = render(<CompactMediaCard data={withCounts} onClick={() => {}} />)
    const [, comments, shares, saves] = [...statsRow(container).children] as HTMLElement[]
    expect(comments.className).toContain('@[126px]:flex')
    expect(shares.className).toContain('@[178px]:flex')
    expect(saves.className).toContain('@[232px]:flex')
    for (const el of [comments, shares, saves]) expect(el.className).toContain('hidden')
  })

  // Hidden, not lost.
  it('keeps every number reachable in the row title', () => {
    const { container } = render(<CompactMediaCard data={withCounts} onClick={() => {}} />)
    const title = statsRow(container).getAttribute('title') ?? ''
    for (const n of ['1.6K', '160', '41', '427']) expect(title).toContain(n)
  })

  // It lost its box; it must not lose its role.
  it('leaves the share stat a real button', () => {
    const { container } = render(<CompactMediaCard data={withCounts} onClick={() => {}} />)
    const shares = statsRow(container).children[2] as HTMLElement
    expect(shares.tagName).toBe('BUTTON')
  })
})
