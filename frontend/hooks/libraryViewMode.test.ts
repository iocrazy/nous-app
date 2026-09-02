/**
 * My Downloads' view-mode preference, including the one-time migration.
 *
 * Mirrors the Resources-side assertions in contexts/ResourcesContext.test.tsx
 * so the two surfaces cannot drift on the default, the validation, or the
 * storage-failure path — plus the migration, which Resources does not need
 * because its storage key is new.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  readStoredLibraryViewMode,
  serializeLibraryPrefs,
  LIBRARY_PREFS_KEY,
  LIBRARY_VIEW_MODES,
  LIBRARY_VIEW_MODE_VERSION,
  DEFAULT_LIBRARY_VIEW_MODE,
} from './useLibrary'

/** A blob as the PREVIOUS code wrote it: no version stamp. */
function legacyBlob(viewMode: string) {
  return JSON.stringify({ activeTab: 'my-library', selectedTeamId: null, viewMode })
}

/** A blob as THIS code writes it: stamped. */
function stampedBlob(viewMode: string) {
  return JSON.stringify({
    activeTab: 'my-library',
    selectedTeamId: null,
    viewMode,
    viewModeVersion: LIBRARY_VIEW_MODE_VERSION,
  })
}

describe('readStoredLibraryViewMode', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('defaults to the justified (adaptive) layout for a first-time user', () => {
    expect(readStoredLibraryViewMode()).toBe('justified')
    expect(DEFAULT_LIBRARY_VIEW_MODE).toBe('justified')
  })

  it('uses the same mode id as the resource grid, not a second spelling', () => {
    // One layout, one name. See the LIBRARY_VIEW_MODES doc comment.
    expect([...LIBRARY_VIEW_MODES]).toEqual(['justified', 'grid', 'list', 'feed'])
  })

  it('restores each stamped mode instead of the default', () => {
    for (const mode of LIBRARY_VIEW_MODES) {
      localStorage.setItem(LIBRARY_PREFS_KEY, stampedBlob(mode))
      expect(readStoredLibraryViewMode()).toBe(mode)
    }
  })

  it('ignores a stored value that is not a known mode', () => {
    localStorage.setItem(LIBRARY_PREFS_KEY, stampedBlob('mosaic'))
    expect(readStoredLibraryViewMode()).toBe('justified')
  })

  it('ignores a blob with no viewMode at all', () => {
    localStorage.setItem(LIBRARY_PREFS_KEY, JSON.stringify({ activeTab: 'team-library' }))
    expect(readStoredLibraryViewMode()).toBe('justified')
  })

  it('survives a corrupt blob without throwing, and reports it', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    localStorage.setItem(LIBRARY_PREFS_KEY, '{not json')

    expect(readStoredLibraryViewMode()).toBe('justified')
    expect(errSpy).toHaveBeenCalled()
    errSpy.mockRestore()
  })

  it('survives storage being unavailable (privacy mode)', () => {
    const getSpy = vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
      throw new Error('storage disabled')
    })
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    expect(readStoredLibraryViewMode()).toBe('justified')
    expect(errSpy).toHaveBeenCalled()

    getSpy.mockRestore()
    errSpy.mockRestore()
  })
})

describe('one-time migration off the manufactured `grid` preference', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('treats an UNVERSIONED stored grid as the effect artefact it is', () => {
    // Every existing user has this on disk, written by a mount effect with no
    // click behind it. Without the migration the new default reaches nobody.
    localStorage.setItem(LIBRARY_PREFS_KEY, legacyBlob('grid'))
    expect(readStoredLibraryViewMode()).toBe('justified')
  })

  it('keeps a VERSIONED stored grid — a real choice survives forever', () => {
    localStorage.setItem(LIBRARY_PREFS_KEY, stampedBlob('grid'))
    expect(readStoredLibraryViewMode()).toBe('grid')
  })

  it('keeps unversioned list and feed — neither was ever the default', () => {
    // Storing one of these required an actual click, so it is a real
    // preference even without a stamp.
    for (const mode of ['list', 'feed'] as const) {
      localStorage.setItem(LIBRARY_PREFS_KEY, legacyBlob(mode))
      expect(readStoredLibraryViewMode()).toBe(mode)
    }
  })

  it('is idempotent for a user who never interacts', () => {
    // Nothing stamps the blob until something changes, so the migration must
    // give the same answer on every load rather than oscillating.
    localStorage.setItem(LIBRARY_PREFS_KEY, legacyBlob('grid'))
    expect(readStoredLibraryViewMode()).toBe('justified')
    expect(readStoredLibraryViewMode()).toBe('justified')
    expect(readStoredLibraryViewMode()).toBe('justified')
  })

  it('round-trips: once the blob is stamped with grid, grid sticks', () => {
    // The end-to-end shape of "user migrates to justified, then deliberately
    // picks grid" — the write goes through serializeLibraryPrefs.
    localStorage.setItem(LIBRARY_PREFS_KEY, legacyBlob('grid'))
    expect(readStoredLibraryViewMode()).toBe('justified')

    localStorage.setItem(
      LIBRARY_PREFS_KEY,
      serializeLibraryPrefs({ activeTab: 'my-library', selectedTeamId: null, viewMode: 'grid' }),
    )
    expect(readStoredLibraryViewMode()).toBe('grid')
  })

  it('accepts any numeric stamp, so a future version is not re-migrated', () => {
    localStorage.setItem(
      LIBRARY_PREFS_KEY,
      JSON.stringify({ viewMode: 'grid', viewModeVersion: LIBRARY_VIEW_MODE_VERSION + 1 }),
    )
    expect(readStoredLibraryViewMode()).toBe('grid')
  })
})

describe('serializeLibraryPrefs', () => {
  it('always stamps the version, so the migration runs at most once', () => {
    const blob = JSON.parse(
      serializeLibraryPrefs({ activeTab: 'my-library', selectedTeamId: null, viewMode: 'justified' }),
    )
    expect(blob.viewModeVersion).toBe(LIBRARY_VIEW_MODE_VERSION)
  })

  it('preserves the pre-existing keys the blob has always carried', () => {
    const blob = JSON.parse(
      serializeLibraryPrefs({ activeTab: 'team-library', selectedTeamId: 't1', viewMode: 'list' }),
    )
    expect(blob).toMatchObject({
      activeTab: 'team-library',
      selectedTeamId: 't1',
      viewMode: 'list',
    })
  })
})
