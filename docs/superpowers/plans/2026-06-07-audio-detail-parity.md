# Uploaded-Audio Detail Parity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the uploaded-audio detail page (`ResourceDetailPage.tsx`) to parity with the downloaded-audio page: bitrate badge (not resolution), a user-uploadable cover, a Lyrics tab fed by an uploaded `.lrc`, and a trimmed tab set (Overview + Lyrics only).

**Architecture:** New `resources` columns (`audio_bitrate_kbps`, `lyrics_json`). Backend probes audio bitrate on upload via the existing ffprobe pattern, adds an LRC parser + cover/lyrics endpoints on the `/resources` router. Frontend extracts a dumb `LyricsView` renderer (shared by the soda download tab and the new upload tab), fixes the badge, gates tabs, and adds cover/LRC upload affordances. Download lyrics storage is untouched; only the display component is shared.

**Tech Stack:** PostgreSQL (Supabase migration), FastAPI + supabase-py, ffprobe (asyncio subprocess), React 19 + TypeScript, pytest, vitest.

Spec: `docs/superpowers/specs/2026-06-07-audio-detail-parity-design.md`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `supabase/migrations/270_resource_audio_bitrate_lyrics.sql` (create) | add 2 columns |
| `backend/app/services/lrc_parser.py` (create) | parse LRC text → `{lrc, lines}` |
| `backend/tests/test_lrc_parser.py` (create) | parser unit tests |
| `backend/app/services/library/resources_service.py` (modify) | audio bitrate probe on upload |
| `backend/app/api/resources_crud_router.py` (modify) | `POST /{id}/cover`, `POST`+`GET /{id}/lyrics` |
| `backend/app/repositories/resources_repository.py` (reuse `update_resource`) | persist columns |
| `backend/tests/test_resource_media_endpoints.py` (create) | endpoint tests |
| `frontend/types.ts` (modify) | `Resource.audio_bitrate_kbps`, `Resource.lyrics_json` |
| `frontend/services/resourceService.ts` (modify) | `uploadResourceCover/uploadResourceLyrics/getResourceLyrics` |
| `frontend/components/LyricsView.tsx` (create) | dumb synced-lyrics renderer |
| `frontend/components/LyricsView.test.tsx` (create) | render/sync unit test |
| `frontend/components/SodaLyricsTab.tsx` (modify) | fetch → feed `LyricsView` |
| `frontend/components/ResourceDetailPage.tsx` (modify) | badge, tab gating, Lyrics tab, cover+LRC affordances |
| `frontend/public/locales/{en,zh}.json` (modify) | i18n keys |

---

## Phase A — Backend

### Task A1: Migration — add columns

**Files:** Create `supabase/migrations/270_resource_audio_bitrate_lyrics.sql` (bump number if 270 taken)

- [ ] **Step 1: Write the migration**

```sql
-- Audio bitrate (kbps) probed on upload, and user-supplied lyrics for uploaded
-- audio. cover_image_path already exists (mig 044) and is reused for covers.
ALTER TABLE resources ADD COLUMN IF NOT EXISTS audio_bitrate_kbps integer;
ALTER TABLE resources ADD COLUMN IF NOT EXISTS lyrics_json jsonb;

COMMENT ON COLUMN resources.audio_bitrate_kbps IS 'Audio bitrate in kbps, ffprobed on upload for audio/* resources';
COMMENT ON COLUMN resources.lyrics_json IS 'User lyrics for uploaded audio: {lrc: text, lines: [{text, line_start_ms}]}';

NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 2: Verify number is free**

Run: `ls supabase/migrations/ | sort | tail -3`
Expected: highest is `269_*`; if `270_*` exists, rename to next free number.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/270_resource_audio_bitrate_lyrics.sql
git commit -m "feat(resources): add audio_bitrate_kbps + lyrics_json columns (mig 270)"
```

---

### Task A2: LRC parser + tests (TDD)

**Files:** Create `backend/app/services/lrc_parser.py`, `backend/tests/test_lrc_parser.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_lrc_parser.py
from app.services.lrc_parser import parse_lrc


def test_parses_timestamped_lines():
    lrc = "[00:12.50]Hello world\n[01:05.00]Second line"
    out = parse_lrc(lrc)
    assert out["lrc"] == lrc
    assert out["lines"] == [
        {"text": "Hello world", "line_start_ms": 12500},
        {"text": "Second line", "line_start_ms": 65000},
    ]


def test_skips_metadata_tags_and_blank_lines():
    lrc = "[ar:Artist]\n[ti:Title]\n\n[00:01.00]Only line"
    out = parse_lrc(lrc)
    assert out["lines"] == [{"text": "Only line", "line_start_ms": 1000}]


def test_multi_timestamp_line_expands():
    lrc = "[00:01.00][00:10.00]Repeat"
    out = parse_lrc(lrc)
    assert out["lines"] == [
        {"text": "Repeat", "line_start_ms": 1000},
        {"text": "Repeat", "line_start_ms": 10000},
    ]


def test_sorts_by_time_and_handles_3digit_ms():
    lrc = "[00:10.000]B\n[00:02.500]A"
    out = parse_lrc(lrc)
    assert [l["line_start_ms"] for l in out["lines"]] == [2500, 10000]


def test_plain_text_without_timestamps_becomes_untimed_lines():
    lrc = "line one\nline two"
    out = parse_lrc(lrc)
    assert out["lines"] == [
        {"text": "line one", "line_start_ms": None},
        {"text": "line two", "line_start_ms": None},
    ]


def test_empty_raises():
    import pytest
    with pytest.raises(ValueError):
        parse_lrc("   \n  ")
```

- [ ] **Step 2: Run, expect FAIL**

Run: `cd backend && uv run pytest tests/test_lrc_parser.py -v`
Expected: FAIL — module `app.services.lrc_parser` not found.

- [ ] **Step 3: Implement the parser**

```python
# backend/app/services/lrc_parser.py
"""Parse LRC lyrics text into the {lrc, lines} shape the frontend LyricsView
consumes. Tolerates metadata tags ([ar:]/[ti:]/[al:]/[by:]/[offset:]),
multi-timestamp lines, 2- or 3-digit milliseconds, and plain (untimed) text."""
import re
from typing import Optional, TypedDict

_TS = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
_META = re.compile(r"^\[[a-zA-Z]+:")


class LyricLine(TypedDict):
    text: str
    line_start_ms: Optional[int]


def _ts_to_ms(m: int, s: int, frac: Optional[str]) -> int:
    ms = 0
    if frac is not None:
        ms = int(frac.ljust(3, "0")[:3])
    return (m * 60 + s) * 1000 + ms


def parse_lrc(raw: str) -> dict:
    if not raw or not raw.strip():
        raise ValueError("empty lyrics")
    lines: list[LyricLine] = []
    has_timestamp = False
    for line in raw.splitlines():
        stamps = list(_TS.finditer(line))
        text = _TS.sub("", line).strip()
        if stamps:
            has_timestamp = True
            for st in stamps:
                lines.append(
                    {
                        "text": text,
                        "line_start_ms": _ts_to_ms(
                            int(st.group(1)), int(st.group(2)), st.group(3)
                        ),
                    }
                )
        else:
            stripped = line.strip()
            if not stripped or _META.match(stripped):
                continue  # blank or metadata-only line
            lines.append({"text": stripped, "line_start_ms": None})
    if not lines:
        raise ValueError("no lyric lines parsed")
    if has_timestamp:
        lines.sort(key=lambda x: (x["line_start_ms"] is None, x["line_start_ms"] or 0))
    return {"lrc": raw, "lines": lines}
```

- [ ] **Step 4: Run, expect PASS**

Run: `cd backend && uv run pytest tests/test_lrc_parser.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/services/lrc_parser.py tests/test_lrc_parser.py && uv run isort app/services/lrc_parser.py tests/test_lrc_parser.py && uv run ruff check app/services/lrc_parser.py tests/test_lrc_parser.py
git add backend/app/services/lrc_parser.py backend/tests/test_lrc_parser.py
git commit -m "feat(resources): LRC parser for uploaded-audio lyrics"
```

---

### Task A3: Probe audio bitrate on upload

**Files:** Modify `backend/app/services/library/resources_service.py`

Context: `_extract_video_metadata(self, filepath)` (~line 976) ffprobes video for `duration_seconds` + `resolution`. The upload flow calls metadata extraction and the result is merged into the `update_resource` payload. We add audio bitrate extraction and include `audio_bitrate_kbps` for audio files.

- [ ] **Step 1: Read the current metadata-extraction call site**

Read `backend/app/services/library/resources_service.py` around `_extract_video_metadata` (~976-1011) AND find where it's called in `upload_resource` (grep `_extract_video_metadata` in the file). Note the variable that holds the extracted dict and how it reaches `update_resource`.

- [ ] **Step 2: Extend extraction to include audio bitrate**

In `_extract_video_metadata`, inside the `for stream in info.get("streams", [])` loop, after the existing video branch add:

```python
            if stream.get("codec_type") == "audio":
                # Prefer stream bit_rate; fall back to format bit_rate below.
                br = stream.get("bit_rate")
                if br:
                    try:
                        result["audio_bitrate_kbps"] = round(int(br) / 1000)
                    except (TypeError, ValueError):
                        pass
```

And after the loop, before `return result`, add a format-level fallback:

```python
        if "audio_bitrate_kbps" not in result:
            fbr = fmt.get("bit_rate")
            if fbr:
                try:
                    result["audio_bitrate_kbps"] = round(int(fbr) / 1000)
                except (TypeError, ValueError):
                    pass
```

(The method already swallows exceptions and returns `{}` on ffprobe failure — bitrate is best-effort, non-fatal, matching the spec.)

- [ ] **Step 3: Ensure the column is whitelisted into the update**

At the call site found in Step 1, confirm the extracted dict is merged into the `update_resource` data dict. If the upload code copies specific keys (allow-list) rather than spreading the whole dict, add `audio_bitrate_kbps` to that allow-list (and `resolution`/`duration_seconds` are already there — match them). If it spreads the whole dict, no change needed. Document which case applied in the commit message.

- [ ] **Step 4: Verify the upload module imports + parses**

Run: `cd backend && uv run python -c "import app.services.library.resources_service"`
Expected: no error.
Run lint: `cd backend && uv run black app/services/library/resources_service.py && uv run ruff check app/services/library/resources_service.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/library/resources_service.py
git commit -m "feat(resources): probe audio bitrate (kbps) on upload"
```

---

### Task A4: Cover upload endpoint

**Files:** Modify `backend/app/api/resources_crud_router.py`

Context: router prefix `/resources`, mutations use `auth: AuthDep`, `_scope: ScopedRequestDep`, `repo = ResourcesRepository()`, `await repo.get_resource_by_id(id)` then `await repo.update_resource(id, {...})`, return `{"success": True, "data": result}`. Files store under `{settings.DOWNLOAD_PATH}/{relative}`; cover convention = write next to the source like thumbnails (`abs_path.parent / "cover.<ext>"`), store the relative path in `cover_image_path`.

- [ ] **Step 1: Add the endpoint**

Add near the other `/resources/{resource_id}` mutations (after the PATCH handler ~line 639). Imports: ensure `from fastapi import UploadFile, File` and `from pathlib import Path` and `from app.core.config import settings` (match existing imports in the file; add only missing ones).

```python
@router.post("/{resource_id}/cover")
async def upload_resource_cover(
    resource_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
    file: UploadFile = File(...),
):
    """Upload/replace the cover image for a resource. Stores next to the source
    file and sets cover_image_path."""
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Cover must be an image")
    repo = ResourcesRepository()
    resource = await repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if not resource.get("file_path"):
        raise HTTPException(status_code=400, detail="Resource has no storage path")

    ext = (file.filename or "cover").rsplit(".", 1)[-1].lower()
    if ext not in {"jpg", "jpeg", "png", "webp", "gif"}:
        ext = "jpg"
    abs_src = Path(settings.DOWNLOAD_PATH) / resource["file_path"]
    cover_abs = abs_src.parent / f"cover.{ext}"
    cover_abs.parent.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    cover_abs.write_bytes(data)
    rel = str(cover_abs.relative_to(Path(settings.DOWNLOAD_PATH)))

    result = await repo.update_resource(resource_id, {"cover_image_path": rel})
    return {"success": True, "data": result}
```

- [ ] **Step 2: Verify import/registration**

Run: `cd backend && uv run python -c "from app.api.resources_crud_router import router; print([r.path for r in router.routes if 'cover' in r.path])"`
Expected: a `/resources/{resource_id}/cover` path prints, no import error.

- [ ] **Step 3: Lint + commit**

```bash
cd backend && uv run black app/api/resources_crud_router.py && uv run isort app/api/resources_crud_router.py && uv run ruff check app/api/resources_crud_router.py
git add backend/app/api/resources_crud_router.py
git commit -m "feat(resources): POST /resources/{id}/cover upload endpoint"
```

---

### Task A5: Lyrics upload + get endpoints + tests

**Files:** Modify `backend/app/api/resources_crud_router.py`; create `backend/tests/test_resource_media_endpoints.py`

- [ ] **Step 1: Write the failing endpoint tests** (mock the repo + parser integration)

```python
# backend/tests/test_resource_media_endpoints.py
from app.services.lrc_parser import parse_lrc


def test_parse_lrc_shape_for_endpoint():
    # The lyrics endpoint stores exactly parse_lrc(text); lock the contract.
    out = parse_lrc("[00:01.00]hi")
    assert set(out.keys()) == {"lrc", "lines"}
    assert out["lines"][0]["line_start_ms"] == 1000
```

(Endpoint-level HTTP tests need the app + auth fixtures; if the repo has no existing router test harness, keep this contract test and rely on the parser unit tests + manual verification for the HTTP layer. Check `backend/tests/` for an existing FastAPI `TestClient` fixture — if one exists, add real POST/GET tests using it; otherwise this contract test is the committed coverage.)

- [ ] **Step 2: Run, expect PASS** (parser already exists)

Run: `cd backend && uv run pytest tests/test_resource_media_endpoints.py -v`
Expected: PASS.

- [ ] **Step 3: Add the lyrics endpoints**

```python
@router.post("/{resource_id}/lyrics")
async def upload_resource_lyrics(
    resource_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
    file: UploadFile = File(...),
):
    """Upload an .lrc file; parse it and store on the resource."""
    from app.services.lrc_parser import parse_lrc

    repo = ResourcesRepository()
    resource = await repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    raw = (await file.read()).decode("utf-8", errors="replace")
    try:
        lyrics = parse_lrc(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid LRC: {e}")
    result = await repo.update_resource(resource_id, {"lyrics_json": lyrics})
    return {"success": True, "data": {"lyrics_json": lyrics, "resource": result}}


@router.get("/{resource_id}/lyrics")
async def get_resource_lyrics(
    resource_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Return the resource's stored lyrics_json (or empty)."""
    repo = ResourcesRepository()
    resource = await repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    return {"success": True, "data": resource.get("lyrics_json")}
```

- [ ] **Step 4: Verify registration**

Run: `cd backend && uv run python -c "from app.api.resources_crud_router import router; print(sorted(r.path for r in router.routes if 'lyrics' in r.path))"`
Expected: `['/resources/{resource_id}/lyrics', '/resources/{resource_id}/lyrics']` (POST + GET).

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/api/resources_crud_router.py tests/test_resource_media_endpoints.py && uv run isort app/api/resources_crud_router.py tests/test_resource_media_endpoints.py && uv run ruff check app/api/resources_crud_router.py tests/test_resource_media_endpoints.py
git add backend/app/api/resources_crud_router.py backend/tests/test_resource_media_endpoints.py
git commit -m "feat(resources): POST/GET /resources/{id}/lyrics endpoints"
```

---

## Phase B — Frontend

### Task B1: Types

**Files:** Modify `frontend/types.ts` (the `Resource` interface, ~line 318-365)

- [ ] **Step 1: Add fields**

In the `Resource` interface, after `resolution: string | null;` add:

```typescript
  audio_bitrate_kbps?: number | null;
  lyrics_json?: { lrc: string; lines: Array<{ text: string; line_start_ms: number | null }> } | null;
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json 2>&1 | grep "types.ts" || echo OK` → `OK`

```bash
git add frontend/types.ts
git commit -m "feat(resources): add audio_bitrate_kbps + lyrics_json to Resource type"
```

---

### Task B2: Service functions + test

**Files:** Modify `frontend/services/resourceService.ts`, `frontend/services/resourceService.test.ts` (create if absent)

Context: `uploadResource` (~790) shows the multipart pattern — build headers from `getAuthHeaders()` but DROP `Content-Type` so the browser sets the multipart boundary. JSON GET uses normal `getAuthHeaders()`.

- [ ] **Step 1: Write the failing test**

```ts
import { describe, it, expect, vi } from 'vitest';
import { getResourceLyrics } from './resourceService';

vi.mock('./parserService', () => ({ getAuthHeaders: async () => ({ 'Content-Type': 'application/json' }) }));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://test' }));

describe('getResourceLyrics', () => {
  it('GETs /resources/{id}/lyrics and returns data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ success: true, data: { lrc: 'x', lines: [] } }),
    }));
    const out = await getResourceLyrics('77');
    expect(out).toEqual({ lrc: 'x', lines: [] });
  });
});
```

- [ ] **Step 2: Run, expect FAIL**

Run: `cd frontend && npx vitest run services/resourceService.test.ts`
Expected: FAIL — `getResourceLyrics is not a function`.

- [ ] **Step 3: Implement**

Add to `frontend/services/resourceService.ts` (near `uploadResource`). Use the same multipart-header filtering for the two uploads:

```ts
async function multipartHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = {};
  const auth = await getAuthHeaders();
  Object.entries(auth).forEach(([k, v]) => {
    if (k.toLowerCase() !== 'content-type') headers[k] = v as string;
  });
  return headers;
}

export async function uploadResourceCover(resourceId: string, file: File): Promise<Resource> {
  const apiUrl = getApiUrl();
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/cover`, {
    method: 'POST', headers: await multipartHeaders(), body: fd,
  });
  if (!res.ok) throw new Error('Failed to upload cover');
  return (await res.json()).data;
}

export async function uploadResourceLyrics(
  resourceId: string, file: File,
): Promise<{ lrc: string; lines: Array<{ text: string; line_start_ms: number | null }> }> {
  const apiUrl = getApiUrl();
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/lyrics`, {
    method: 'POST', headers: await multipartHeaders(), body: fd,
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({ detail: 'Failed to upload lyrics' }));
    throw new Error(e.detail || `HTTP ${res.status}`);
  }
  return (await res.json()).data.lyrics_json;
}

export async function getResourceLyrics(
  resourceId: string,
): Promise<{ lrc: string; lines: Array<{ text: string; line_start_ms: number | null }> } | null> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/resources/${resourceId}/lyrics`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) return null;
  return (await res.json()).data ?? null;
}
```

(Ensure `getAuthHeaders` and `getApiUrl` are already imported in this file — they are, used by `uploadResource`.)

- [ ] **Step 4: Run, expect PASS**

Run: `cd frontend && npx vitest run services/resourceService.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/services/resourceService.ts frontend/services/resourceService.test.ts
git commit -m "feat(resources): cover + lyrics service functions"
```

---

### Task B3: Extract `LyricsView` + refactor `SodaLyricsTab`

**Files:** Create `frontend/components/LyricsView.tsx`, `frontend/components/LyricsView.test.tsx`; modify `frontend/components/SodaLyricsTab.tsx`

Context: `SodaLyricsTab` currently fetches via `getMediaLyrics(mediaId)` then renders synced lines. Extract the render+sync (lines ~71-89 sync/scroll, ~251-270 render) into a dumb `LyricsView` that takes `lines` + `currentTime` + `theme` + `variant`.

- [ ] **Step 1: Write the failing test for `LyricsView`**

```tsx
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { LyricsView } from './LyricsView';

describe('LyricsView', () => {
  const lines = [
    { text: 'first', line_start_ms: 0 },
    { text: 'second', line_start_ms: 10000 },
  ];
  it('renders all lines', () => {
    const { getByText } = render(<LyricsView lines={lines} />);
    expect(getByText('first')).toBeTruthy();
    expect(getByText('second')).toBeTruthy();
  });
  it('marks the active line by currentTime', () => {
    const { getByText } = render(<LyricsView lines={lines} currentTime={11} />);
    // active line is the last whose start <= currentTime → "second"
    expect(getByText('second').className).toContain('font-semibold');
  });
});
```

- [ ] **Step 2: Run, expect FAIL**

Run: `cd frontend && npx vitest run components/LyricsView.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `LyricsView`** (move the sync + render core out of SodaLyricsTab verbatim)

```tsx
import React, { useEffect, useRef } from 'react';
import type { SodaTheme } from '../utils/sodaTheme'; // match SodaLyricsTab's theme import path

export interface LyricLine { text: string; line_start_ms: number | null }

interface LyricsViewProps {
  lines: LyricLine[];
  currentTime?: number;
  theme?: SodaTheme;
  variant?: 'card' | 'bare';
}

export const LyricsView: React.FC<LyricsViewProps> = ({ lines, currentTime, theme, variant = 'card' }) => {
  const activeLineRef = useRef<HTMLParagraphElement>(null);
  const synced = typeof currentTime === 'number' && currentTime > 0;
  let activeIndex = -1;
  if (synced) {
    for (let i = 0; i < lines.length; i++) {
      const startMs = lines[i].line_start_ms;
      if (typeof startMs === 'number' && startMs / 1000 <= currentTime!) activeIndex = i;
      else if (typeof startMs === 'number') break;
    }
  }
  useEffect(() => {
    if (activeIndex < 0) return;
    activeLineRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [activeIndex]);

  return (
    <div className={variant === 'card' ? 'space-y-2' : 'space-y-3 text-center'}>
      {lines.map((line, index) => {
        const isActive = index === activeIndex;
        const themedStyle = theme
          ? { color: isActive ? theme.lyricActive : synced ? theme.lyricNormal : undefined }
          : undefined;
        return (
          <p
            key={index}
            ref={isActive ? activeLineRef : undefined}
            style={themedStyle}
            className={`leading-relaxed transition-all duration-200 ${
              isActive ? 'text-base font-semibold' : 'text-sm font-medium'
            } ${theme ? '' : isActive ? 'text-white' : synced ? 'text-zinc-500' : 'text-zinc-300'}`}
          >
            {line.text || ' '}
          </p>
        );
      })}
    </div>
  );
};
```

(Read SodaLyricsTab's actual `SodaTheme` import + the exact render JSX before finalizing, and match its classNames/variant handling so the soda view is visually unchanged.)

- [ ] **Step 4: Run, expect PASS**

Run: `cd frontend && npx vitest run components/LyricsView.test.tsx`
Expected: PASS (if `@testing-library/react` is the project's React test util — confirm by grepping an existing `.test.tsx` that renders a component; match its import).

- [ ] **Step 5: Refactor `SodaLyricsTab` to use `LyricsView`**

In `SodaLyricsTab.tsx`, keep the fetch (`getMediaLyrics`), loading/error/fetch-button logic, and the card/header chrome, but replace the inline `lines.map(...)` render block with `<LyricsView lines={lines} currentTime={currentTime} theme={theme} variant={variant} />`. Remove the now-duplicated sync/scroll code (activeIndex/activeLineRef/scroll effect) from SodaLyricsTab — it lives in LyricsView now.

- [ ] **Step 6: Verify nothing broke**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -E "LyricsView|SodaLyricsTab" || echo OK` → `OK`
Run: `cd frontend && npx vitest run components/LyricsView.test.tsx` → PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/components/LyricsView.tsx frontend/components/LyricsView.test.tsx frontend/components/SodaLyricsTab.tsx
git commit -m "refactor(lyrics): extract shared LyricsView from SodaLyricsTab"
```

---

### Task B4: ResourceDetailPage — badge, tabs, Lyrics tab, cover/LRC affordances

**Files:** Modify `frontend/components/ResourceDetailPage.tsx`

- [ ] **Step 1: Fix the audio badge (~line 1254-1259)**

Replace the resolution badge block so audio shows bitrate (or nothing) instead of resolution:

```tsx
    <DetailBadge>{resource.file_type || resource.mime_type?.split('/').pop() || 'File'}</DetailBadge>
    {isAudio
      ? (resource.audio_bitrate_kbps ? <DetailBadge variant="accent">{resource.audio_bitrate_kbps}kbps</DetailBadge> : null)
      : (resource.resolution && <DetailBadge variant="accent">{resource.resolution.replace(/:/g, 'x')}</DetailBadge>)}
```

- [ ] **Step 2: Add an `isUploadedAudio` flag + a 'lyrics' tab value**

Near `const isAudio = ...` (~755) add:
```tsx
  const isUploadedAudio = isAudio && resource.source_type === 'upload';
```
Find the `rightTab` state type (grep `setRightTab` / `useState`) and add `'lyrics'` to its union if it's typed.

- [ ] **Step 3: Gate the tab bar (~1144-1195)**

Wrap so uploaded audio shows ONLY Overview + a new Lyrics tab. Concretely: when `isUploadedAudio`, render the Overview button + a Lyrics button and SKIP Review/Transcript/Analysis. Otherwise render the existing set. Add the Lyrics button (Music icon, label via i18n) that does `setRightTab('lyrics')`. Keep the existing `(isVideo || isAudio)` Transcript/Analysis block gated additionally on `!isUploadedAudio`, and gate the Review button on `!isUploadedAudio`.

Pattern for the Lyrics button (place where the others are):
```tsx
  {isUploadedAudio && (
    <button
      onClick={() => setRightTab('lyrics')}
      className={`flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors border-b-2 ${
        rightTab === 'lyrics' ? 'border-indigo-500 text-indigo-400'
          : 'border-transparent text-zinc-400 hover:text-zinc-200 hover:border-zinc-700'}`}
    >
      <Music size={16} />
      {t('resources.detail.lyrics', 'Lyrics')}
    </button>
  )}
```
(Import `Music` from `lucide-react` if not already imported.)

- [ ] **Step 4: Render the Lyrics tab content**

Find where tab bodies render (grep `rightTab === 'review'` / `rightTab === 'transcript'`). Add a `rightTab === 'lyrics'` body that:
- loads lyrics on mount/when tab opens via `getResourceLyrics(resource.id)` into local state (seed from `resource.lyrics_json` if present),
- if lyrics present: render `<LyricsView lines={lyrics.lines} currentTime={audioCurrentTime} />` (use the page's existing audio current-time state if available; otherwise omit currentTime — it still renders unsynced),
- if absent: show an empty state with an "Upload .lrc" file input that calls `uploadResourceLyrics(resource.id, file)` then sets local state + toasts.

```tsx
  {rightTab === 'lyrics' && (
    <div className="p-4 overflow-y-auto">
      {lyrics?.lines?.length ? (
        <LyricsView lines={lyrics.lines} />
      ) : (
        <div className="text-center text-zinc-500 text-sm py-8 space-y-3">
          <p>{t('resources.detail.noLyrics', 'No lyrics yet')}</p>
          <label className="inline-block px-3 py-1.5 rounded bg-indigo-600 text-white text-xs cursor-pointer hover:bg-indigo-500">
            {t('resources.detail.uploadLrc', 'Upload .lrc')}
            <input type="file" accept=".lrc,text/plain" className="hidden" onChange={async (e) => {
              const f = e.target.files?.[0]; if (!f) return;
              try { const data = await uploadResourceLyrics(resource.id, f); setLyrics(data); addToast(t('resources.detail.lyricsUploaded', 'Lyrics uploaded'), 'success'); }
              catch (err) { addToast(err instanceof Error ? err.message : 'Failed', 'error'); }
            }} />
          </label>
        </div>
      )}
    </div>
  )}
```
Add `const [lyrics, setLyrics] = useState(resource.lyrics_json ?? null)` and a `useEffect` to `getResourceLyrics` when `rightTab === 'lyrics' && !lyrics`. Import `LyricsView`, `getResourceLyrics`, `uploadResourceLyrics`, and `useToast` (the page likely already uses toast — match its existing toast usage).

- [ ] **Step 5: Add the cover-upload affordance**

Near the AudioHero usage (~201-209), add an "Upload cover" control for `isAudio` (any audio, upload or download? scope to `source_type === 'upload'` to be safe). A small button/overlay with a hidden file input calling `uploadResourceCover(resource.id, file)` then refreshing the resource (call the page's existing resource-refresh/onUpdated handler, or update local state's `cover_image_path`).

```tsx
  {resource.source_type === 'upload' && isAudio && (
    <label className="absolute bottom-2 right-2 text-xs px-2 py-1 rounded bg-black/60 text-white cursor-pointer hover:bg-black/80">
      {t('resources.detail.uploadCover', 'Cover')}
      <input type="file" accept="image/*" className="hidden" onChange={async (e) => {
        const f = e.target.files?.[0]; if (!f) return;
        try { const updated = await uploadResourceCover(resource.id, f); onResourceUpdated?.(updated); addToast(t('resources.detail.coverUpdated', 'Cover updated'), 'success'); }
        catch { addToast('Failed to upload cover', 'error'); }
      }} />
    </label>
  )}
```
(Use the page's actual "resource updated" callback name — grep for how PATCH results refresh the view; `onResourceUpdated` is a placeholder for that real handler. Ensure the AudioHero container is `relative` positioned.)

- [ ] **Step 6: Typecheck + commit**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json 2>&1 | grep "ResourceDetailPage" || echo OK` → `OK`

```bash
git add frontend/components/ResourceDetailPage.tsx
git commit -m "feat(resources): uploaded-audio detail — bitrate badge, Lyrics tab, cover+lrc upload, trimmed tabs"
```

---

### Task B5: i18n keys

**Files:** Modify `frontend/public/locales/en.json`, `frontend/public/locales/zh.json`

- [ ] **Step 1: Add keys** under the existing `resources.detail` object (create it if absent):

en.json:
```json
"lyrics": "Lyrics",
"noLyrics": "No lyrics yet",
"uploadLrc": "Upload .lrc",
"lyricsUploaded": "Lyrics uploaded",
"uploadCover": "Cover",
"coverUpdated": "Cover updated"
```
zh.json (same keys):
```json
"lyrics": "歌词",
"noLyrics": "暂无歌词",
"uploadLrc": "上传 .lrc",
"lyricsUploaded": "歌词已上传",
"uploadCover": "封面",
"coverUpdated": "封面已更新"
```

- [ ] **Step 2: Validate JSON + commit**

Run: `cd frontend && node -e "JSON.parse(require('fs').readFileSync('public/locales/en.json','utf8'));JSON.parse(require('fs').readFileSync('public/locales/zh.json','utf8'));console.log('OK')"` → `OK`

```bash
git add frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(resources): i18n for audio detail lyrics + cover"
```

---

### Task B6: Full-suite verification

- [ ] **Step 1: Run full frontend suite + backend tests**

Run: `cd frontend && npx vitest run` → all pass
Run: `cd backend && uv run pytest tests/test_lrc_parser.py tests/test_resource_media_endpoints.py -q` → all pass
Run typecheck: `cd frontend && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -E "LyricsView|ResourceDetailPage|resourceService|types.ts" || echo "no new errors in touched files"`

- [ ] **Step 2: Manual verification (Vercel preview after PR)**

Upload an audio file → detail page shows `{n}kbps` (not WxH), tabs = Overview + Lyrics only. Upload a cover image → shows on AudioHero. Upload a `.lrc` → Lyrics tab shows synced lines.

---

## Self-review notes

- **Spec coverage:** bitrate column+probe+badge (A1/A3/B4-1), cover endpoint+UI (A4/B4-5), lyrics column+parser+endpoints+upload UI+shared renderer (A1/A2/A5/B2/B3/B4-4), tab gating (B4-3), download path untouched (B3 keeps SodaLyricsTab fetch). All covered.
- **Lint gate:** every backend task runs black+isort+ruff before commit (per the Backend Lint & Deps CI gate — see learnings).
- **Type consistency:** `lyrics_json` shape `{lrc, lines:[{text, line_start_ms}]}` identical across migration comment, types.ts, parser output, service return, LyricsView props. `audio_bitrate_kbps` int everywhere.
- **Read-before-edit:** Tasks A3, B3, B4 explicitly say to read the real call sites / SodaLyricsTab render / rightTab state before editing, because exact surrounding code (allow-list vs spread, SodaTheme import, resource-refresh callback name) must be matched — these are the spots where the engineer must adapt to reality rather than assume.
