/**
 * My Downloads' view-mode preference. Mirrors the Resources-side assertions in
 * contexts/ResourcesContext.test.tsx so the two surfaces cannot drift on the
 * default, the validation, or the storage-failure path.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  readStoredLibraryViewMode,
  LIBRARY_PREFS_KEY,
  LIBRARY_VIEW_MODES,
} from './useLibrary'

describe('readStoredLibraryViewMode', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('defaults to the adaptive layout for a first-time user', () => {
    expect(readStoredLibraryViewMode()).toBe('adaptive')
  })

  it('offers adaptive alongside the three pre-existing modes', () => {
    // Adding adaptive must not have dropped one of the modes users already use.
    expect([...LIBRARY_VIEW_MODES]).toEqual(['adaptive', 'grid', 'list', 'feed'])
  })

  it('restores each stored mode instead of the default', () => {
    for (const mode of LIBRARY_VIEW_MODES) {
      localStorage.setItem(LIBRARY_PREFS_KEY, JSON.stringify({ viewMode: mode }))
      expect(readStoredLibraryViewMode()).toBe(mode)
    }
  })

  it('keeps an existing user on grid — the new default only affects newcomers', () => {
    localStorage.setItem(
      LIBRARY_PREFS_KEY,
      JSON.stringify({ activeTab: 'my-library', viewMode: 'grid' }),
    )
    expect(readStoredLibraryViewMode()).toBe('grid')
  })

  it('ignores a stored value that is not a known mode', () => {
    localStorage.setItem(LIBRARY_PREFS_KEY, JSON.stringify({ viewMode: 'mosaic' }))
    expect(readStoredLibraryViewMode()).toBe('adaptive')
  })

  it('ignores a blob with no viewMode at all', () => {
    localStorage.setItem(LIBRARY_PREFS_KEY, JSON.stringify({ activeTab: 'team-library' }))
    expect(readStoredLibraryViewMode()).toBe('adaptive')
  })

  it('survives a corrupt blob without throwing, and reports it', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    localStorage.setItem(LIBRARY_PREFS_KEY, '{not json')

    expect(readStoredLibraryViewMode()).toBe('adaptive')
    // Never a silent swallow.
    expect(errSpy).toHaveBeenCalled()
    errSpy.mockRestore()
  })

  it('survives storage being unavailable (privacy mode)', () => {
    const getSpy = vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
      throw new Error('storage disabled')
    })
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    expect(readStoredLibraryViewMode()).toBe('adaptive')
    expect(errSpy).toHaveBeenCalled()

    getSpy.mockRestore()
    errSpy.mockRestore()
  })
})
