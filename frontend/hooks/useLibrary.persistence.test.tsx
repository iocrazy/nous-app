/**
 * useLibrary's preferences persistence effect, rendered for real.
 *
 * `readStoredLibraryViewMode` is unit-tested next door, but the migration only
 * works if the WRITE side behaves: the effect must stamp `viewModeVersion`
 * exactly once and must not write when nothing changed. That second half is the
 * root cause this branch fixed — the old effect wrote the whole blob on every
 * mount, which manufactured a `viewMode: "grid"` for users who never chose one
 * and made the new default unreachable for anyone who had opened the app.
 *
 * A comment cannot pin that. Counting real `setItem` calls can.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, act } from '@testing-library/react'
import React from 'react'

vi.mock('../supabaseClient', () => ({
  getSupabaseClient: () => null,
  isSupabaseConfigured: () => false,
}))
vi.mock('../services/dataService', () => ({
  fetchLibraryPaginated: vi.fn().mockResolvedValue({ items: [], nextCursor: null, total: 0 }),
  updateItem: vi.fn(),
  deleteItem: vi.fn(),
}))
vi.mock('../services/collectionService', () => ({
  fetchMyCollections: vi.fn().mockResolvedValue([]),
  createCollection: vi.fn(),
  fetchVideoCollections: vi.fn().mockResolvedValue([]),
  addVideoToCollection: vi.fn(),
  removeVideoFromCollection: vi.fn(),
}))
vi.mock('../constants', () => ({ MOCK_LIBRARY: [] }))

import {
  useLibrary,
  LIBRARY_PREFS_KEY,
  LIBRARY_VIEW_MODE_VERSION,
  type LibraryViewMode,
} from './useLibrary'

let setViewMode: (m: LibraryViewMode) => void = () => {}

function Harness() {
  const { libraryViewMode, setLibraryViewMode } = useLibrary({
    isAuthenticated: false,
    selectedTeamId: null,
  })
  setViewMode = setLibraryViewMode
  return <span data-testid="mode">{libraryViewMode}</span>
}

/** Count only writes to the preferences key; other hooks touch storage too. */
function spyOnPrefWrites() {
  const calls: string[] = []
  const real = window.localStorage.setItem.bind(window.localStorage)
  vi.spyOn(window.localStorage, 'setItem').mockImplementation((key: string, value: string) => {
    if (key === LIBRARY_PREFS_KEY) calls.push(value)
    real(key, value)
  })
  return calls
}

function storedPrefs() {
  return JSON.parse(localStorage.getItem(LIBRARY_PREFS_KEY) ?? '{}')
}

describe('useLibrary — preferences persistence', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('stamps the version on mount for a legacy unversioned blob', () => {
    // The migration's durability depends on this write landing exactly once.
    localStorage.setItem(
      LIBRARY_PREFS_KEY,
      JSON.stringify({ activeTab: 'my-library', selectedTeamId: null, viewMode: 'grid' }),
    )
    const writes = spyOnPrefWrites()

    render(<Harness />)

    expect(writes).toHaveLength(1)
    expect(storedPrefs()).toMatchObject({
      viewMode: 'justified',
      viewModeVersion: LIBRARY_VIEW_MODE_VERSION,
    })
  })

  it('does NOT write on mount when the stored blob already matches', () => {
    // The regression guard. Re-introducing an unconditional write turns this
    // red, and an unconditional write is what forged the fake `grid` preference.
    localStorage.setItem(
      LIBRARY_PREFS_KEY,
      JSON.stringify({
        activeTab: 'my-library',
        selectedTeamId: null,
        viewMode: 'grid',
        viewModeVersion: LIBRARY_VIEW_MODE_VERSION,
      }),
    )
    const writes = spyOnPrefWrites()

    render(<Harness />)

    expect(writes).toHaveLength(0)
    expect(storedPrefs().viewMode).toBe('grid')
  })

  it('writes once for a first-time user, stamping the default', () => {
    const writes = spyOnPrefWrites()

    render(<Harness />)

    expect(writes).toHaveLength(1)
    expect(storedPrefs()).toMatchObject({
      viewMode: 'justified',
      viewModeVersion: LIBRARY_VIEW_MODE_VERSION,
    })
  })

  it('persists a real user choice, and only that choice', () => {
    const writes = spyOnPrefWrites()
    render(<Harness />)
    const afterMount = writes.length

    act(() => setViewMode('grid'))
    expect(writes).toHaveLength(afterMount + 1)
    expect(storedPrefs()).toMatchObject({
      viewMode: 'grid',
      viewModeVersion: LIBRARY_VIEW_MODE_VERSION,
    })

    // Re-selecting the same mode changes nothing, so nothing is written.
    act(() => setViewMode('grid'))
    expect(writes).toHaveLength(afterMount + 1)
  })

  it('keeps the pre-existing keys the blob has always carried', () => {
    render(<Harness />)
    expect(storedPrefs()).toMatchObject({ activeTab: 'my-library', selectedTeamId: null })
  })

  it('reports a storage failure instead of swallowing it', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded')
    })

    expect(() => render(<Harness />)).not.toThrow()
    expect(errSpy).toHaveBeenCalled()
  })
})
