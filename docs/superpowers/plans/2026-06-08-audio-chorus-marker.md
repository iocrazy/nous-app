# Uploaded-Audio Chorus Marker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users set/clear a chorus marker on uploaded audio, persist it, and display it with the same amber marker the download view uses.

**Architecture:** New `resources.chorus_start_ms` column (ms). Dedicated gated `PUT /resources/{id}/chorus` endpoint (set/clear). The shared `AudioWaveformPlayer` gains opt-in `chorusEditable` + `onChorusChange` that render Set/Clear buttons in the **full** layout's bottom control bar; `AudioHero` forwards them; `ResourceDetailPage` wires display + persistence for uploaded audio only. Mobile is covered automatically (uploaded audio uses the `full` layout on all screens).

**Tech Stack:** Supabase/Postgres migration, FastAPI + Pydantic, React 19 + TypeScript + Vite, vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-06-08-audio-chorus-marker-design.md`

---

### Task 1: Migration — `resources.chorus_start_ms`

**Files:**
- Create: `supabase/migrations/273_resource_chorus_start.sql`

- [ ] **Step 1: Write the migration**

```sql
-- 273_resource_chorus_start.sql
-- User-settable chorus (highlight) start point for uploaded audio, in ms.
-- Mirrors the unit of parsed_media.metadata.chorus.start (ms) so the frontend
-- divides by 1000 and reuses the existing AudioWaveformPlayer amber marker.
ALTER TABLE resources ADD COLUMN IF NOT EXISTS chorus_start_ms integer;

COMMENT ON COLUMN resources.chorus_start_ms IS 'Chorus/highlight start in ms for uploaded audio; NULL = unset';

NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 2: Commit**

```bash
git add supabase/migrations/273_resource_chorus_start.sql
git commit -m "feat(db): add resources.chorus_start_ms (mig 273)"
```

---

### Task 2: Backend — schema, endpoint, tests

**Files:**
- Modify: `backend/app/schemas/resources.py` (add `ChorusUpdate`)
- Modify: `backend/app/api/resources_crud_router.py` (add `PUT /{resource_id}/chorus`)
- Test: `backend/tests/test_resource_chorus_endpoint.py`

- [ ] **Step 1: Write the failing tests**

Mirror the structure of `backend/tests/test_resource_media_endpoints.py` (read it first for the exact app/client/auth/`check_media_access`/repo mocking conventions, then match them). The tests must cover:

```python
# backend/tests/test_resource_chorus_endpoint.py
# Use the same fixtures/mocks test_resource_media_endpoints.py uses:
#   - app TestClient, auth override, ResourcesRepository patched,
#     app.api.media_permissions.check_media_access patched.

def test_chorus_set_persists(...):
    # upload audio resource, check_media_access -> True
    # PUT /api/v1/resources/{id}/chorus  body {"chorus_start_ms": 42000}
    # -> 200, repo.update_resource called with {"chorus_start_ms": 42000}

def test_chorus_clear_with_null(...):
    # PUT body {"chorus_start_ms": null}
    # -> 200, repo.update_resource called with {"chorus_start_ms": None}

def test_chorus_rejects_non_owner(...):
    # check_media_access -> False
    # -> 403

def test_chorus_rejects_non_upload(...):
    # resource source_type='web'
    # -> 400

def test_chorus_rejects_non_audio(...):
    # resource mime_type='video/mp4', source_type='upload'
    # -> 400

def test_chorus_rejects_negative_ms(...):
    # body {"chorus_start_ms": -5}
    # -> 422 (Pydantic ge=0) or 400 — assert >= 400 and repo.update_resource NOT called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_resource_chorus_endpoint.py -v`
Expected: FAIL (endpoint 404 / schema missing).

- [ ] **Step 3: Add the `ChorusUpdate` schema**

In `backend/app/schemas/resources.py`, after `ResourceUpdate`:

```python
class ChorusUpdate(BaseModel):
    """Body for PUT /resources/{id}/chorus. None clears the marker."""

    chorus_start_ms: Optional[int] = Field(None, ge=0)
```

- [ ] **Step 4: Add the endpoint**

In `backend/app/api/resources_crud_router.py`, add after `get_resource_lyrics` (~line 723). Import `ChorusUpdate` alongside the existing `ResourceUpdate` import at the top of the file.

```python
@router.put("/{resource_id}/chorus")
async def set_resource_chorus(
    resource_id: str,
    data: ChorusUpdate,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Set or clear (null) the chorus marker for an uploaded audio resource."""
    from app.api.media_permissions import check_media_access

    repo = ResourcesRepository()
    resource = await repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")
    if (resource.get("source_type") != "upload") or not (
        resource.get("mime_type") or ""
    ).startswith("audio/"):
        raise HTTPException(
            status_code=400, detail="Chorus is only settable on uploaded audio"
        )

    result = await repo.update_resource(
        resource_id, {"chorus_start_ms": data.chorus_start_ms}
    )
    return {"success": True, "data": result}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_resource_chorus_endpoint.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run black app/schemas/resources.py app/api/resources_crud_router.py tests/test_resource_chorus_endpoint.py && uv run isort app/schemas/resources.py app/api/resources_crud_router.py tests/test_resource_chorus_endpoint.py && uv run ruff check app/schemas/resources.py app/api/resources_crud_router.py tests/test_resource_chorus_endpoint.py
cd .. && git add backend/app/schemas/resources.py backend/app/api/resources_crud_router.py backend/tests/test_resource_chorus_endpoint.py
git commit -m "feat(resources): PUT /resources/{id}/chorus set/clear endpoint (upload audio, owner-gated)"
```

---

### Task 3: Frontend — types + service

**Files:**
- Modify: `frontend/types.ts` (3 Resource-shaped interfaces — the same that carry `audio_bitrate_kbps`)
- Modify: `frontend/services/resourceService.ts` (add `setResourceChorus`)
- Test: `frontend/services/resourceService.chorus.test.ts`

- [ ] **Step 1: Add the type field**

In `frontend/types.ts`, add to each of the three Resource-shaped interfaces (locate the three `audio_bitrate_kbps: number | null;` lines and add directly after each):

```typescript
  chorus_start_ms?: number | null;
```

- [ ] **Step 2: Write the failing service test**

```typescript
// frontend/services/resourceService.chorus.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { setResourceChorus } from './resourceService';

vi.mock('./parserService', () => ({ getAuthHeaders: vi.fn().mockResolvedValue({}) }));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));

describe('setResourceChorus', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('PUTs the chorus ms and returns the updated resource', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ data: { id: '1', chorus_start_ms: 42000 } }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const res = await setResourceChorus('1', 42000);
    expect(fetchMock).toHaveBeenCalledWith(
      'http://api.test/api/v1/resources/1/chorus',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ chorus_start_ms: 42000 }) }),
    );
    expect(res.chorus_start_ms).toBe(42000);
  });

  it('PUTs null to clear', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ data: { id: '1', chorus_start_ms: null } }) });
    vi.stubGlobal('fetch', fetchMock);
    await setResourceChorus('1', null);
    expect(fetchMock).toHaveBeenCalledWith(
      'http://api.test/api/v1/resources/1/chorus',
      expect.objectContaining({ body: JSON.stringify({ chorus_start_ms: null }) }),
    );
  });

  it('throws on non-ok', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
    await expect(setResourceChorus('1', 1000)).rejects.toThrow();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run services/resourceService.chorus.test.ts`
Expected: FAIL (`setResourceChorus` not exported).

- [ ] **Step 4: Implement the service function**

In `frontend/services/resourceService.ts`, add next to `updateResource` (mirror its shape):

```typescript
export async function setResourceChorus(
  resourceId: string,
  chorusMs: number | null,
): Promise<Resource> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/chorus`, {
    method: 'PUT',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify({ chorus_start_ms: chorusMs }),
  });
  if (!response.ok) throw new Error('Failed to set chorus');
  const json = await response.json();
  return json.data;
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npx vitest run services/resourceService.chorus.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/types.ts frontend/services/resourceService.ts frontend/services/resourceService.chorus.test.ts
git commit -m "feat(resources): types + setResourceChorus service"
```

---

### Task 4: `AudioWaveformPlayer` — editable chorus controls

**Files:**
- Modify: `frontend/components/AudioWaveformPlayer.tsx`
- Test: `frontend/components/AudioWaveformPlayer.chorus.test.tsx`

**Context:** The component has two layouts: `compact` (early `if (isCompact) return (...)`) and `full` (default, second return). Add controls **only to the full layout's bottom control bar** (the `<div className="flex items-center gap-4 px-6 py-3 border-t ...">` at ~line 495). The full layout is what `AudioHero` (uploaded audio) renders on every screen size.

- [ ] **Step 1: Write the failing test**

The player runs Web Audio decode in an effect; jsdom lacks `AudioContext`. Stub it so the component mounts. The Set/Clear buttons live in the control bar and are not gated by decode/duration, so they render synchronously.

```tsx
// frontend/components/AudioWaveformPlayer.chorus.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import { AudioWaveformPlayer } from './AudioWaveformPlayer';

beforeEach(() => {
  // Minimal AudioContext stub so the decode effect doesn't throw in jsdom.
  vi.stubGlobal('AudioContext', class {
    decodeAudioData() { return Promise.resolve({ getChannelData: () => new Float32Array(0), length: 0 }); }
    close() { return Promise.resolve(); }
  });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ arrayBuffer: async () => new ArrayBuffer(0) }));
});

describe('AudioWaveformPlayer chorus controls', () => {
  it('shows "Set chorus" and calls onChorusChange with current time when no chorus', () => {
    const onChorusChange = vi.fn();
    const { getByText } = render(
      <AudioWaveformPlayer src="blob:a" filename="a" chorusEditable onChorusChange={onChorusChange} />,
    );
    fireEvent.click(getByText('Set chorus'));
    expect(onChorusChange).toHaveBeenCalledWith(0); // currentTime starts at 0
  });

  it('shows "Clear" and calls onChorusChange(null) when chorus set', () => {
    const onChorusChange = vi.fn();
    const { getByText } = render(
      <AudioWaveformPlayer src="blob:a" filename="a" chorusStartSec={12} chorusEditable onChorusChange={onChorusChange} />,
    );
    fireEvent.click(getByText('Clear'));
    expect(onChorusChange).toHaveBeenCalledWith(null);
  });

  it('renders no chorus buttons when not editable', () => {
    const { queryByText } = render(
      <AudioWaveformPlayer src="blob:a" filename="a" chorusStartSec={12} />,
    );
    expect(queryByText('Set chorus')).toBeNull();
    expect(queryByText('Clear')).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/AudioWaveformPlayer.chorus.test.tsx`
Expected: FAIL (props/buttons not present).

- [ ] **Step 3: Add props to the interface**

In `AudioWaveformPlayerProps` (after `playbackRate?`):

```typescript
  /** When true, render Set/Clear chorus controls in the full-layout control bar. */
  chorusEditable?: boolean;
  /** Set (seconds) or clear (null) the chorus marker. */
  onChorusChange?: (sec: number | null) => void;
```

Destructure `chorusEditable` and `onChorusChange` in the component signature.

- [ ] **Step 4: Add controls to the full-layout bottom control bar**

In the full layout's bottom control bar, insert before the Playback-rate button (after the `<span className="flex-1 min-w-0" />` spacer) so the chorus controls sit on the right cluster:

```tsx
        {/* Chorus set/clear — uploaded-audio only (opt-in via chorusEditable) */}
        {chorusEditable && (
          chorusStartSec === undefined ? (
            <button
              type="button"
              onClick={() => onChorusChange?.(currentTime)}
              className="px-2 py-0.5 text-xs font-medium text-amber-300 hover:text-amber-200 bg-zinc-800 hover:bg-zinc-700 rounded transition-colors whitespace-nowrap"
            >
              Set chorus
            </button>
          ) : (
            <button
              type="button"
              onClick={() => onChorusChange?.(null)}
              className="px-2 py-0.5 text-xs font-medium text-amber-300 hover:text-amber-200 bg-zinc-800 hover:bg-zinc-700 rounded transition-colors whitespace-nowrap"
            >
              Clear
            </button>
          )
        )}
```

NOTE: the on-screen button text ("Set chorus" / "Clear") will be replaced with i18n in Task 6; keep literal strings here so this task's test passes, then Task 6 swaps to `t(...)` and updates the test.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/AudioWaveformPlayer.chorus.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/components/AudioWaveformPlayer.tsx frontend/components/AudioWaveformPlayer.chorus.test.tsx
git commit -m "feat(audio): opt-in chorus Set/Clear controls in AudioWaveformPlayer full layout"
```

---

### Task 5: `AudioHero` — forward chorus-edit props

**Files:**
- Modify: `frontend/components/AudioHero.tsx`

- [ ] **Step 1: Add props to `AudioHeroProps`**

After `onCoverClick?`:

```typescript
  /** Forwarded to AudioWaveformPlayer — enables chorus Set/Clear controls. */
  chorusEditable?: boolean;
  /** Forwarded to AudioWaveformPlayer — set (sec) or clear (null) the chorus. */
  onChorusChange?: (sec: number | null) => void;
```

- [ ] **Step 2: Destructure + forward**

Add `chorusEditable` and `onChorusChange` to the destructured props, then pass them to `<AudioWaveformPlayer>` (which already receives `chorusStartSec`):

```tsx
          chorusStartSec={chorusStartSec}
          chorusEditable={chorusEditable}
          onChorusChange={onChorusChange}
```

- [ ] **Step 3: Verify build (no dedicated test — pure pass-through)**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep AudioHero || echo "ok"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/AudioHero.tsx
git commit -m "feat(audio): AudioHero forwards chorusEditable/onChorusChange"
```

---

### Task 6: `ResourceDetailPage` — wire display + edit + i18n

**Files:**
- Modify: `frontend/components/ResourceDetailPage.tsx`
- Modify: `frontend/components/AudioWaveformPlayer.tsx` (swap literals → i18n)
- Modify: `frontend/components/AudioWaveformPlayer.chorus.test.tsx` (match i18n)
- Modify: `frontend/public/locales/en.json`, `frontend/public/locales/zh.json`

**Context:** `FilePreview` (audio branch, ~line 207) renders `<AudioHero ...>`. `FilePreview` already receives `resource`, `onCoverUpdated` (which is `setResource` — reuse it to refresh after a chorus change), `t`, `addToast`, and already declares `const isUpload = resource.source_type === 'upload';` + `coverInputRef` (from the cover-upload work). Reuse `isUpload` — do not introduce a new local. Use `setResourceChorus` from resourceService.

- [ ] **Step 1: Add i18n keys**

`frontend/public/locales/en.json` under `resources.detail`:

```json
"setChorus": "Set chorus",
"clearChorus": "Clear",
"chorusSet": "Chorus marked",
"chorusCleared": "Chorus removed"
```

`frontend/public/locales/zh.json` under `resources.detail`:

```json
"setChorus": "标记高潮",
"clearChorus": "清除",
"chorusSet": "已标记高潮点",
"chorusCleared": "已移除高潮点"
```

- [ ] **Step 2: Pass i18n labels into the player (avoid hardcoded English in shared component)**

The shared `AudioWaveformPlayer` should not import page i18n. Add two optional label props instead, defaulting to English:

In `AudioWaveformPlayerProps`:
```typescript
  /** Labels for the chorus controls (i18n supplied by the caller). */
  setChorusLabel?: string;
  clearChorusLabel?: string;
```
Destructure with defaults: `setChorusLabel = 'Set chorus'`, `clearChorusLabel = 'Clear'`. Replace the literal button texts from Task 4 with `{setChorusLabel}` / `{clearChorusLabel}`.

Update `AudioWaveformPlayer.chorus.test.tsx` to keep using the defaults (`getByText('Set chorus')` / `getByText('Clear')` still valid since defaults are English).

Forward both labels through `AudioHero` (add `setChorusLabel?`, `clearChorusLabel?` to props + pass-through).

- [ ] **Step 3: Wire display + edit in `FilePreview` audio branch**

Import `setResourceChorus` at the top of `resourceService` imports in `ResourceDetailPage.tsx`. In the audio branch (`if (mime.startsWith('audio/'))`), compute `const isUploadAudio = resource.source_type === 'upload';` and extend the `<AudioHero>` props:

```tsx
        <AudioHero
          src={fileUrl}
          title={resource.filename}
          coverUrl={
            resource.cover_image_path && resource.id
              ? getResourceCoverUrl(String(resource.id), undefined, resource.updated_at)
              : resource.thumbnail_path || undefined
          }
          duration={resource.duration_seconds ?? undefined}
          onCoverClick={isUpload ? () => coverInputRef.current?.click() : undefined}
          chorusStartSec={
            resource.chorus_start_ms != null ? resource.chorus_start_ms / 1000 : undefined
          }
          chorusEditable={isUpload}
          setChorusLabel={t('resources.detail.setChorus', 'Set chorus')}
          clearChorusLabel={t('resources.detail.clearChorus', 'Clear')}
          onChorusChange={isUpload ? async (sec) => {
            try {
              const ms = sec == null ? null : Math.round(sec * 1000);
              const updated = await setResourceChorus(resource.id, ms);
              onCoverUpdated?.(updated);
              addToast(
                t(ms == null ? 'resources.detail.chorusCleared' : 'resources.detail.chorusSet',
                  ms == null ? 'Chorus removed' : 'Chorus marked'),
                'success',
              );
            } catch (err) {
              console.error('Failed to set chorus:', err);
              addToast('Failed to set chorus', 'error');
            }
          } : undefined}
        />
```

(`isUpload` is the existing local from the cover work — reuse it; do not introduce `isUploadAudio` if `isUpload` already exists in that branch.)

- [ ] **Step 4: Typecheck + full frontend tests**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep -E "ResourceDetailPage|AudioHero|AudioWaveformPlayer" || echo "ok"`
Then: `cd frontend && npx vitest run`
Expected: `ok` and all tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/ResourceDetailPage.tsx frontend/components/AudioWaveformPlayer.tsx frontend/components/AudioWaveformPlayer.chorus.test.tsx frontend/components/AudioHero.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(resources): wire chorus display + set/clear on uploaded-audio detail (i18n)"
```

---

### Task 7: Verify + version bump

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Full suites green**

Run: `cd frontend && npx vitest run` (expect all pass) and `cd backend && uv run pytest tests/test_resource_chorus_endpoint.py -v` (expect pass).

- [ ] **Step 2: Bump version**

Bump `frontend/package.json` `version` (patch, e.g. `0.23.49` → `0.23.50`).

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json
git commit -m "chore: bump version (uploaded-audio chorus marker)"
```

---

## Notes for the executor

- **Migration apply:** the SQL file + PR is enough — CI auto-applies on merge. Do not SSH-apply manually.
- **PostgREST cache:** the migration's `NOTIFY pgrst` covers new-column visibility; no `select("*")` allow-list to touch (`repo.update_resource` is pass-through).
- **Backend lint gate:** run `black`/`isort`/`ruff` on touched `.py` before commit (CI `Backend Lint & Deps` fast-fails otherwise).
- **Scope guard:** do not modify `DownloadDetailPage` or `MobileAudioScreen` — downloads are out of scope; chorus controls are opt-in via `chorusEditable`.
