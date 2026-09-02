import { describe, it, expect } from 'vitest'
import {
  aspectRatioOf,
  needsAspectMeasurement,
  parseResolution,
  DEFAULT_IMAGE_ASPECT,
  DEFAULT_VIDEO_ASPECT,
  NON_VISUAL_ASPECT,
} from './resourceAspect'

describe('parseResolution', () => {
  it('reads the wire format the download path writes', () => {
    // ytdlp_service stores `${width}x${height}`.
    expect(parseResolution('1920x1080')).toBeCloseTo(16 / 9, 5)
  })

  it('accepts the colon and multiplication-sign spellings', () => {
    expect(parseResolution('1080:1920')).toBeCloseTo(0.5625, 5)
    expect(parseResolution('1920 × 1080')).toBeCloseTo(16 / 9, 5)
  })

  it('returns null for absent or unparseable values', () => {
    expect(parseResolution(null)).toBeNull()
    expect(parseResolution(undefined)).toBeNull()
    expect(parseResolution('')).toBeNull()
    expect(parseResolution('unknown')).toBeNull()
    expect(parseResolution('0x0')).toBeNull()
  })

  it('clamps an extreme panorama into the display range', () => {
    expect(parseResolution('10000x100')).toBe(3)
    expect(parseResolution('100x10000')).toBe(0.4)
  })
})

describe('aspectRatioOf priority order', () => {
  it('prefers the server resolution over a measured value', () => {
    const ar = aspectRatioOf(
      { resolution: '1920x1080', mime_type: 'image/jpeg' },
      1 /* measured, should be ignored */,
    )
    expect(ar).toBeCloseTo(16 / 9, 5)
  })

  it('uses the measured value when the server sent no resolution', () => {
    // This is the My Uploads case: the upload router never records dimensions.
    const ar = aspectRatioOf({ resolution: null, mime_type: 'image/png' }, 0.75)
    expect(ar).toBeCloseTo(0.75, 5)
  })

  it('falls back to 4:3 for an unmeasured image', () => {
    expect(aspectRatioOf({ mime_type: 'image/png' })).toBe(DEFAULT_IMAGE_ASPECT)
  })

  it('falls back to 16:9 for an unmeasured video', () => {
    expect(aspectRatioOf({ mime_type: 'video/mp4' })).toBe(DEFAULT_VIDEO_ASPECT)
  })

  it('gives non-visual files a square tile and never measures them', () => {
    for (const mime of [
      'application/zip',
      'audio/mpeg',
      'application/pdf',
      'text/plain',
      'application/x-mediahub-gallery',
    ]) {
      expect(aspectRatioOf({ mime_type: mime })).toBe(NON_VISUAL_ASPECT)
      expect(needsAspectMeasurement({ mime_type: mime })).toBe(false)
    }
  })

  it('ignores a nonsense measured value rather than collapsing a row', () => {
    for (const bad of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(aspectRatioOf({ mime_type: 'image/png' }, bad)).toBe(DEFAULT_IMAGE_ASPECT)
    }
  })

  it('clamps a measured value into the display range', () => {
    expect(aspectRatioOf({ mime_type: 'image/png' }, 50)).toBe(3)
  })

  it('handles a missing resource', () => {
    expect(aspectRatioOf(undefined)).toBe(NON_VISUAL_ASPECT)
  })
})

describe('needsAspectMeasurement', () => {
  it('is false once the server gave a usable resolution', () => {
    expect(
      needsAspectMeasurement({ resolution: '1920x1080', mime_type: 'image/jpeg' }),
    ).toBe(false)
  })

  it('is true for a visual file with no resolution', () => {
    expect(needsAspectMeasurement({ resolution: null, mime_type: 'image/jpeg' })).toBe(true)
    expect(needsAspectMeasurement({ mime_type: 'video/mp4' })).toBe(true)
  })

  it('is true when the stored resolution is unparseable', () => {
    expect(needsAspectMeasurement({ resolution: 'N/A', mime_type: 'image/jpeg' })).toBe(true)
  })

  it('is false for a missing resource', () => {
    expect(needsAspectMeasurement(undefined)).toBe(false)
  })
})
