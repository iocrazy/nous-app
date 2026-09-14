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
