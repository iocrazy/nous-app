import { describe, it, expect } from 'vitest'
import {
  aspectRatioOf,
  matchesCurrentAspect,
  ASPECT_MATCH_TOLERANCE,
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

describe('matchesCurrentAspect — skip layout churn for a confirming measurement', () => {
  it('is true when the measurement lands on the placeholder', () => {
    // A genuine 4:3 photo measuring in against the 4:3 placeholder: reporting
    // it would repartition the tail to produce the same picture.
    expect(matchesCurrentAspect(DEFAULT_IMAGE_ASPECT, 4 / 3)).toBe(true)
  })

  it('is true just inside the tolerance and false just outside', () => {
    const current = DEFAULT_IMAGE_ASPECT
    const inside = current * (1 + ASPECT_MATCH_TOLERANCE * 0.9)
    const outside = current * (1 + ASPECT_MATCH_TOLERANCE * 1.1)
    expect(matchesCurrentAspect(current, inside)).toBe(true)
    expect(matchesCurrentAspect(current, outside)).toBe(false)
  })

  it('is false for a real correction, so portrait uploads still re-layout', () => {
    // The case that matters: a portrait phone photo under a 4:3 placeholder.
    expect(matchesCurrentAspect(DEFAULT_IMAGE_ASPECT, 0.75)).toBe(false)
  })

  it('compares against the CLAMPED measurement, matching what the layout uses', () => {
    // 50 clamps to 3; against a current of 3 that is a confirmation, not a change.
    expect(matchesCurrentAspect(3, 50)).toBe(true)
  })

  it('is false for nonsense input rather than silently skipping', () => {
    for (const bad of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(matchesCurrentAspect(DEFAULT_IMAGE_ASPECT, bad)).toBe(false)
    }
  })
})
