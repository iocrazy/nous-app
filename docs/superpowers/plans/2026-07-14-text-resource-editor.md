# Text Resource Preview + Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give text-type resources (`.md`, `.txt`, `.env`, `.json`, code, …) an in-place preview + editor on the resource detail page, reusing the platform's TipTap engine, with save-as-new-version and overwrite-current-version.

**Architecture:** One editor engine (TipTap), two modes. `.md` → markdown WYSIWYG via the existing `Inspiration/NoteEditor` (lossy on exact formatting, fine for prose). Every other text file → a single byte-preserving TipTap code block (load/save through a pure doc↔text round-trip, never markdown serialization) with optional `lowlight` highlight. Files > 512 KB → read-only truncated preview. Editing is creator-only; saving reuses `POST /{id}/versions` for new versions and a new `PUT /{id}/versions/{version_id}/content` for overwrite.

**Tech Stack:** React 19 + TypeScript + Vite, `@tiptap/react` v3, `@tiptap/markdown`, `@tiptap/extension-code-block-lowlight` + `lowlight` (all already installed); FastAPI + SQLAlchemy backend; vitest (frontend) + pytest (backend).

## Global Constraints

- Work in worktree `/Volumes/program/project-code/repos/mediahub/.worktrees/ops1-smoke`, branch `feat/text-resource-editor` (already branched from `origin/master`).
- Frontend tests: `cd frontend && npx vitest run <path>`. Backend tests: `cd backend && uv run pytest <path> -q` (run with `backend/.env` **moved aside** if it contains `SUPAVISOR_DATABASE_URL`, else integration tests wake up — but the tests here are pure unit tests with mocked repos, so they pass either way).
- UI text is English; user-facing strings go through i18n `t('...')` with an English fallback literal.
- No new npm/pip dependencies — every library used here is already in `frontend/package.json`.
- Snowflake IDs are strings end-to-end; never `Number()`-coerce them. Backend binds bigint via existing repo helpers.
- Text-editor mount cap: **512 KB** (`TEXT_EDIT_MAX_BYTES = 512 * 1024`). Backend overwrite endpoint reuses the existing `MAX_UPLOAD_SIZE = 500 * 1024 * 1024` guard (the 512 KB cap is purely a client-side editor-mount decision).
- Markdown extensions: `md`, `markdown`. Everything else `text/*` or a known code/config extension → plain-code mode. Unknown-extension-but-text → plain-code mode (no highlight, byte-safe). Non-text mime → untouched (existing image/video/audio/pdf/download branches).

---

## File Structure

Frontend (new):
- `frontend/utils/textResourceMode.ts` — pure classifier: `(filename, mime, sizeBytes) → 'markdown' | 'plaintext' | 'oversize' | null` + `codeLangForExtension(ext) → string | null`.
- `frontend/utils/tiptapPlainText.ts` — pure byte-preserving round-trip: `plainTextToDoc(text)` / `docToPlainText(json)`.
- `frontend/components/resources/PlainTextResourceEditor.tsx` — single-code-block TipTap, byte-preserving, lowlight highlight, save bar.
- `frontend/components/resources/MarkdownResourceEditor.tsx` — thin wrapper over `NoteEditor` + save bar.
- `frontend/components/resources/TextResourcePreview.tsx` — dispatcher: fetch text, pick mode, read/edit toggle, save actions.

Frontend (modified):
- `frontend/services/resourceService.ts` — add `saveTextAsNewVersion(...)` and `overwriteVersionContent(...)`.
- `frontend/components/ResourceDetailPage.tsx` — `FilePreview` routes text mimes to `TextResourcePreview`.

Backend (modified):
- `backend/app/services/library/resources_service.py` — add `overwrite_version_content(...)`.
- `backend/app/api/resources_versions_router.py` — add `PUT /{resource_id}/versions/{version_id}/content`.

---

## Task 1: Text mode classifier util

**Files:**
- Create: `frontend/utils/textResourceMode.ts`
- Test: `frontend/utils/textResourceMode.test.ts`

**Interfaces:**
- Produces: `classifyTextResource(input: { filename: string | null; mime: string | null; sizeBytes: number | null }): 'markdown' | 'plaintext' | 'oversize' | null` and `codeLangForExtension(ext: string): string | null` and `TEXT_EDIT_MAX_BYTES: number`.
- `null` means "not a text resource — leave it to the caller's other branches". `'oversize'` means "text but too big to mount an editor".

- [ ] **Step 1: Write the failing test**

```ts
// frontend/utils/textResourceMode.test.ts
import { describe, expect, it } from 'vitest';
import {
  classifyTextResource,
  codeLangForExtension,
  TEXT_EDIT_MAX_BYTES,
} from './textResourceMode';

describe('classifyTextResource', () => {
  it('classifies markdown by extension', () => {
    expect(classifyTextResource({ filename: 'a.md', mime: '', sizeBytes: 100 })).toBe('markdown');
    expect(classifyTextResource({ filename: 'a.markdown', mime: null, sizeBytes: 100 })).toBe('markdown');
  });
  it('classifies markdown by mime', () => {
    expect(classifyTextResource({ filename: 'x', mime: 'text/markdown', sizeBytes: 10 })).toBe('markdown');
  });
  it('classifies non-markdown text as plaintext', () => {
    for (const f of ['a.env', 'b.json', 'c.log', 'd.txt', 'e.py', 'f.yaml']) {
      expect(classifyTextResource({ filename: f, mime: '', sizeBytes: 10 })).toBe('plaintext');
    }
  });
  it('classifies text/* mime with unknown extension as plaintext', () => {
    expect(classifyTextResource({ filename: 'noext', mime: 'text/plain', sizeBytes: 10 })).toBe('plaintext');
  });
  it('returns null for non-text mime with unknown extension', () => {
    expect(classifyTextResource({ filename: 'blob.bin', mime: 'application/octet-stream', sizeBytes: 10 })).toBeNull();
    expect(classifyTextResource({ filename: 'p.png', mime: 'image/png', sizeBytes: 10 })).toBeNull();
  });
  it('flags oversize text', () => {
    expect(classifyTextResource({ filename: 'big.md', mime: '', sizeBytes: TEXT_EDIT_MAX_BYTES + 1 })).toBe('oversize');
    expect(classifyTextResource({ filename: 'big.json', mime: '', sizeBytes: TEXT_EDIT_MAX_BYTES + 1 })).toBe('oversize');
  });
});

describe('codeLangForExtension', () => {
  it('maps known extensions to lowlight language names', () => {
    expect(codeLangForExtension('json')).toBe('json');
    expect(codeLangForExtension('yaml')).toBe('yaml');
    expect(codeLangForExtension('yml')).toBe('yaml');
    expect(codeLangForExtension('py')).toBe('python');
    expect(codeLangForExtension('js')).toBe('javascript');
    expect(codeLangForExtension('ts')).toBe('typescript');
  });
  it('returns null for unknown extensions', () => {
    expect(codeLangForExtension('env')).toBeNull();
    expect(codeLangForExtension('zzz')).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run utils/textResourceMode.test.ts`
Expected: FAIL — cannot resolve `./textResourceMode`.

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/utils/textResourceMode.ts
// Pure classifier for text-resource preview/editing (spec 2026-07-14).
// Decides which editor mode a resource's detail page should use — never
// mutates anything, no I/O.

export const TEXT_EDIT_MAX_BYTES = 512 * 1024;

const MARKDOWN_EXTS = new Set(['md', 'markdown']);

// Extensions we treat as editable plain text even when the mime isn't text/*.
const PLAINTEXT_EXTS = new Set([
  'txt', 'env', 'json', 'log', 'csv', 'yaml', 'yml', 'ini', 'conf', 'toml',
  'py', 'js', 'jsx', 'ts', 'tsx', 'sh', 'bash', 'html', 'htm', 'css', 'scss',
  'xml', 'svg', 'sql', 'go', 'rs', 'java', 'c', 'cpp', 'h', 'rb', 'php',
]);

// Extension → lowlight language name (lowlight `common` set). Only extensions
// whose grammar ships in `common` map to a name; the rest highlight as plain.
const LOWLIGHT_LANG: Record<string, string> = {
  json: 'json',
  yaml: 'yaml',
  yml: 'yaml',
  py: 'python',
  js: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  html: 'xml',
  htm: 'xml',
  xml: 'xml',
  svg: 'xml',
  css: 'css',
  scss: 'css',
};

function extOf(filename: string | null): string {
  if (!filename) return '';
  const dot = filename.lastIndexOf('.');
  return dot >= 0 ? filename.slice(dot + 1).toLowerCase() : '';
}

export function codeLangForExtension(ext: string): string | null {
  return LOWLIGHT_LANG[ext.toLowerCase()] ?? null;
}

export function classifyTextResource(input: {
  filename: string | null;
  mime: string | null;
  sizeBytes: number | null;
}): 'markdown' | 'plaintext' | 'oversize' | null {
  const mime = (input.mime ?? '').toLowerCase();
  const ext = extOf(input.filename);

  const isMarkdown = MARKDOWN_EXTS.has(ext) || mime === 'text/markdown';
  const isText =
    isMarkdown || mime.startsWith('text/') || PLAINTEXT_EXTS.has(ext);

  if (!isText) return null;

  const size = input.sizeBytes ?? 0;
  if (size > TEXT_EDIT_MAX_BYTES) return 'oversize';

  return isMarkdown ? 'markdown' : 'plaintext';
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run utils/textResourceMode.test.ts`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add frontend/utils/textResourceMode.ts frontend/utils/textResourceMode.test.ts
git commit -m "feat(resources): text-resource mode classifier util"
```

---

## Task 2: Byte-preserving plain-text round-trip util

**Files:**
- Create: `frontend/utils/tiptapPlainText.ts`
- Test: `frontend/utils/tiptapPlainText.test.ts`

**Interfaces:**
- Produces: `plainTextToDoc(text: string, language?: string | null): JSONContent` and `docToPlainText(doc: JSONContent): string`. `JSONContent` is imported from `@tiptap/core`.
- Contract: `docToPlainText(plainTextToDoc(t)) === t` for any string `t` (byte fidelity — the spec's one real risk).

- [ ] **Step 1: Write the failing test**

```ts
// frontend/utils/tiptapPlainText.test.ts
import { describe, expect, it } from 'vitest';
import { plainTextToDoc, docToPlainText } from './tiptapPlainText';

const roundtrip = (t: string) => docToPlainText(plainTextToDoc(t));

describe('tiptapPlainText round-trip (byte fidelity)', () => {
  it('preserves a .env verbatim', () => {
    const t = 'API_KEY=abc123\n# comment with # and *stars*\nPORT=8080\n';
    expect(roundtrip(t)).toBe(t);
  });
  it('preserves JSON with braces, quotes, indentation', () => {
    const t = '{\n  "a": 1,\n  "b": ["x", "y"]\n}';
    expect(roundtrip(t)).toBe(t);
  });
  it('preserves markdown-special characters that WYSIWYG would eat', () => {
    const t = '## not a heading\n* not a bullet\n`code`\n\n\ntrailing blanks';
    expect(roundtrip(t)).toBe(t);
  });
  it('preserves an empty string', () => {
    expect(roundtrip('')).toBe('');
  });
  it('sets the code block language when given', () => {
    const doc = plainTextToDoc('x', 'json');
    expect(doc.content?.[0]?.attrs?.language).toBe('json');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run utils/tiptapPlainText.test.ts`
Expected: FAIL — cannot resolve `./tiptapPlainText`.

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/utils/tiptapPlainText.ts
// Byte-preserving bridge between a raw text string and a single-code-block
// TipTap document (spec 2026-07-14). The whole point: NEVER route
// non-markdown text through the markdown parser/serializer. A code_block
// node stores its content as literal text (newlines are real \n characters,
// whitespace: 'pre'), so building the doc directly and reading text nodes
// back is a lossless round-trip.

import type { JSONContent } from '@tiptap/core';

export function plainTextToDoc(
  text: string,
  language?: string | null,
): JSONContent {
  const codeBlock: JSONContent = {
    type: 'codeBlock',
    attrs: { language: language ?? null },
    // An empty string must produce an empty code block (no text node), or
    // ProseMirror rejects a zero-length text node.
    content: text ? [{ type: 'text', text }] : [],
  };
  return { type: 'doc', content: [codeBlock] };
}

export function docToPlainText(doc: JSONContent): string {
  const block = doc.content?.[0];
  if (!block || block.type !== 'codeBlock' || !block.content) return '';
  // Concatenate every text node's text verbatim — newlines live inside these
  // text nodes for a code block, so this is byte-exact.
  return block.content
    .map((n) => (n.type === 'text' ? n.text ?? '' : ''))
    .join('');
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run utils/tiptapPlainText.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/utils/tiptapPlainText.ts frontend/utils/tiptapPlainText.test.ts
git commit -m "feat(resources): byte-preserving plain-text tiptap round-trip util"
```

---

## Task 3: Backend overwrite-version-content endpoint + service

**Files:**
- Modify: `backend/app/services/library/resources_service.py` (add `overwrite_version_content`)
- Modify: `backend/app/api/resources_versions_router.py` (add `PUT /{resource_id}/versions/{version_id}/content`)
- Test: `backend/tests/test_version_overwrite_content.py`

**Interfaces:**
- Produces (service): `ResourcesService.overwrite_version_content(resource_id: str, version_id: str, user_id: str, file) -> dict` — writes the new bytes to the content-addressed store (or fs fallback) and updates the version row's `file_path` / `file_size_bytes` / `mime_type`; returns the updated version dict. Raises `ValueError("Version not found")` if the version does not belong to the resource.
- Produces (router): `PUT /api/v1/resources/{resource_id}/versions/{version_id}/content` (multipart `file`), guarded by `verify_resource_write_access` (creator-only), returns `{"success": true, "data": <version>}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_version_overwrite_content.py
"""Overwrite-current-version content endpoint (text resource editing).

Pure unit tests: the service's repo + storage helpers are mocked, so no DB.
Mirrors test_version_service.py conventions (patch collaborators, call the
service method directly)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _svc():
    from app.services.library.resources_service import ResourcesService

    svc = ResourcesService()
    return svc


async def test_overwrite_updates_current_version_row(tmp_path):
    svc = _svc()
    svc.repo = MagicMock()
    svc.repo.get_resource_by_id = AsyncMock(
        return_value={"id": "10", "filename": "notes.md"}
    )
    svc.repo.get_version_by_id = AsyncMock(
        return_value={"id": "77", "resource_id": "10", "version_number": 1}
    )
    svc.repo.get_first_resource_item = AsyncMock(
        return_value={"scope_id": "42"}
    )
    svc.repo.update_version = AsyncMock(
        side_effect=lambda vid, data: {"id": vid, **data}
    )

    stored = SimpleNamespace(
        file_path="sb://library/t42/ab/cd/deadbeef.md", size_bytes=12
    )
    file = SimpleNamespace(filename="notes.md", content_type="text/markdown", size=12)

    with (
        patch(
            "app.services.library.resources_service.unified_storage_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.library.resources_service.stream_upload_to_disk",
            AsyncMock(return_value=(12, "deadbeef")),
        ),
        patch(
            "app.services.library.resources_service.sniff_mime",
            return_value="text/markdown",
        ),
        patch(
            "app.services.library.resources_service.store_local_file",
            AsyncMock(return_value=stored),
        ),
    ):
        result = await svc.overwrite_version_content(
            resource_id="10", version_id="77", user_id="u1", file=file
        )

    svc.repo.update_version.assert_awaited_once()
    called_vid, called_data = svc.repo.update_version.await_args.args
    assert called_vid == "77"
    assert called_data["file_path"] == "sb://library/t42/ab/cd/deadbeef.md"
    assert called_data["file_size_bytes"] == 12
    assert result["file_path"] == "sb://library/t42/ab/cd/deadbeef.md"


async def test_overwrite_rejects_version_from_other_resource():
    svc = _svc()
    svc.repo = MagicMock()
    svc.repo.get_resource_by_id = AsyncMock(return_value={"id": "10"})
    svc.repo.get_version_by_id = AsyncMock(
        return_value={"id": "77", "resource_id": "999"}
    )

    file = SimpleNamespace(filename="x", content_type="text/plain", size=1)
    with pytest.raises(ValueError):
        await svc.overwrite_version_content(
            resource_id="10", version_id="77", user_id="u1", file=file
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_version_overwrite_content.py -q`
Expected: FAIL — `AttributeError: 'ResourcesService' object has no attribute 'overwrite_version_content'`.

- [ ] **Step 3: Write the service method**

Open `backend/app/services/library/resources_service.py`. Immediately AFTER the `upload_new_version` method (find its `return` and the next `async def`), add this method inside the `ResourcesService` class (same indentation as `upload_new_version`). It reuses the same imports `upload_new_version` already uses at module scope: `tempfile`, `os`, `Path`, `stream_upload_to_disk`, `sniff_mime`, `sanitize_filename`, `unified_storage_enabled`, `store_local_file`, `MAX_UPLOAD_SIZE`, `mimetypes`, `logger`.

```python
    async def overwrite_version_content(
        self,
        resource_id: str,
        version_id: str,
        user_id: str,
        file,
    ) -> dict:
        """Replace the bytes of an existing version in place (text editing).

        Unlike ``upload_new_version`` this does NOT create a new
        ``resource_versions`` row — it content-addresses the new bytes and
        repoints the given version's ``file_path`` / ``file_size_bytes`` /
        ``mime_type``. The previous object may become orphaned; that is
        harmless and dedup-safe under content addressing (a future GC
        reclaims it). Creator-only enforcement lives in the router guard.
        """
        resource = await self.repo.get_resource_by_id(resource_id)
        if not resource:
            raise ValueError("Resource not found")

        version = await self.repo.get_version_by_id(version_id)
        if not version or str(version.get("resource_id")) != str(resource_id):
            raise ValueError("Version not found")

        safe_name = sanitize_filename(file.filename)

        tmp_fd, tmp_name = tempfile.mkstemp()
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)
        try:
            file_size, file_hash = await stream_upload_to_disk(
                file, tmp_path, MAX_UPLOAD_SIZE
            )
            mime = (
                sniff_mime(tmp_path)
                or file.content_type
                or mimetypes.guess_type(safe_name)[0]
                or ""
            )

            stored = None
            if await unified_storage_enabled():
                item = await self.repo.get_first_resource_item(resource_id)
                if item is not None:
                    try:
                        stored = await store_local_file(
                            scope_id=int(item["scope_id"]),
                            source_path=str(tmp_path),
                            mime=mime,
                            filename=safe_name,
                            sha256=file_hash,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.error(
                            f"[overwrite_version_content] unified-storage write "
                            f"failed, falling back to filesystem: "
                            f"resource={resource_id} error={exc!r}"
                        )

            if stored is not None:
                relative_path = stored.file_path
            else:
                # Filesystem fallback: keep the file next to the resource's
                # existing versioned tree. Content addressing has no name
                # collisions, but the fs path needs a version-scoped folder.
                from app.core.config import settings

                save_dir = (
                    Path(settings.DOWNLOAD_PATH)
                    / "resources"
                    / str(resource_id)
                    / f"v{version.get('version_number', 1)}"
                )
                save_dir.mkdir(parents=True, exist_ok=True)
                target = save_dir / safe_name
                import shutil

                shutil.move(str(tmp_path), str(target))
                relative_path = str(
                    target.relative_to(Path(settings.DOWNLOAD_PATH))
                )
        finally:
            tmp_path.unlink(missing_ok=True)

        updated = await self.repo.update_version(
            version_id,
            {
                "file_path": relative_path,
                "file_size_bytes": file_size,
                "mime_type": mime,
                "filename": safe_name,
            },
        )
        return updated
```

- [ ] **Step 4: Run the service test to verify it passes**

Run: `cd backend && uv run pytest tests/test_version_overwrite_content.py -q`
Expected: PASS (both cases).

- [ ] **Step 5: Add the router endpoint**

Open `backend/app/api/resources_versions_router.py`. After the `upload_version` handler (the `POST /{resource_id}/versions` one, ending before `@router.post("/{resource_id}/versions/{version_number}/set-current")`), add:

```python
@router.put("/{resource_id}/versions/{version_id}/content")
async def overwrite_version_content(
    resource_id: str,
    version_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
    file: UploadFile = File(...),
    _resource_guard: None = Depends(verify_resource_write_access),
):
    """Overwrite an existing version's bytes in place (text editing).

    `_resource_guard` enforces creator-only, same as new-version upload.
    Does not create a new version row — see
    ResourcesService.overwrite_version_content.
    """
    try:
        if file.size and file.size > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413, detail="File too large. Maximum size is 500 MB."
            )
        svc = ResourcesService()
        result = await svc.overwrite_version_content(
            resource_id=resource_id,
            version_id=version_id,
            user_id=auth.user_id,
            file=file,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to overwrite version {version_id} of resource "
            f"{resource_id}: {e}"
        )
        raise HTTPException(status_code=500, detail="Failed to overwrite version")
```

Check the top-of-file imports include `UploadFile` and `File` from fastapi (the `upload_version` handler already uses `File(...)`, so `File` is imported; confirm `UploadFile` is too — if not, add it to the existing `from fastapi import (...)` block).

- [ ] **Step 6: Verify the whole file imports and the router test still passes**

Run: `cd backend && uv run pytest tests/test_version_overwrite_content.py tests/test_versions_authz_wiring.py -q`
Expected: PASS. (The authz-wiring test confirms the guard wiring for versions routes still holds.)

- [ ] **Step 7: Lint + commit**

```bash
cd backend && uv run black --quiet app/services/library/resources_service.py app/api/resources_versions_router.py tests/test_version_overwrite_content.py && uv run flake8 app/services/library/resources_service.py app/api/resources_versions_router.py tests/test_version_overwrite_content.py
cd /Volumes/program/project-code/repos/mediahub/.worktrees/ops1-smoke
git add backend/app/services/library/resources_service.py backend/app/api/resources_versions_router.py backend/tests/test_version_overwrite_content.py
git commit -m "feat(resources): overwrite-current-version content endpoint"
```

---

## Task 4: Frontend service functions (save-as-new-version, overwrite)

**Files:**
- Modify: `frontend/services/resourceService.ts` (add two functions near `uploadNewVersion`, ~line 1305)
- Test: `frontend/services/resourceService.textsave.test.ts`

**Interfaces:**
- Consumes: existing `uploadNewVersion(resourceId, file, notes?)` and `getApiUrl()`, `getAuthHeaders()` (already in the file).
- Produces: `saveTextAsNewVersion(resourceId: string, text: string, filename: string, mime: string, notes?: string): Promise<ResourceVersion>` and `overwriteVersionContent(resourceId: string, versionId: string, text: string, filename: string, mime: string): Promise<ResourceVersion>`.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/services/resourceService.textsave.test.ts
import { afterEach, describe, expect, it, vi } from 'vitest';

// resourceService.ts imports: supabase (side-effect), getApiUrl from
// ../utils/apiConfig, getAuthHeaders from ./parserService. Mock all three.
vi.mock('../supabaseClient', () => ({ supabase: {} }));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));
vi.mock('./parserService', () => ({
  getAuthHeaders: async () => ({ Authorization: 'Bearer T' }),
}));

import { saveTextAsNewVersion, overwriteVersionContent } from './resourceService';

afterEach(() => vi.restoreAllMocks());

describe('saveTextAsNewVersion', () => {
  it('POSTs a File built from the text to the versions endpoint', async () => {
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      expect(String(url)).toContain('/api/v1/resources/10/versions');
      expect(init.method).toBe('POST');
      const body = init.body as FormData;
      const f = body.get('file') as File;
      expect(await f.text()).toBe('hello world');
      expect(f.name).toBe('notes.md');
      return { ok: true, json: async () => ({ data: { id: 'v2' } }) } as Response;
    });
    vi.stubGlobal('fetch', fetchMock);
    const r = await saveTextAsNewVersion('10', 'hello world', 'notes.md', 'text/markdown');
    expect(r.id).toBe('v2');
  });
});

describe('overwriteVersionContent', () => {
  it('PUTs a File built from the text to the content endpoint', async () => {
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      expect(String(url)).toContain('/api/v1/resources/10/versions/77/content');
      expect(init.method).toBe('PUT');
      const f = (init.body as FormData).get('file') as File;
      expect(await f.text()).toBe('overwritten');
      return { ok: true, json: async () => ({ data: { id: '77' } }) } as Response;
    });
    vi.stubGlobal('fetch', fetchMock);
    const r = await overwriteVersionContent('10', '77', 'overwritten', 'a.txt', 'text/plain');
    expect(r.id).toBe('77');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run services/resourceService.textsave.test.ts`
Expected: FAIL — `saveTextAsNewVersion`/`overwriteVersionContent` are not exported.

Note: if the mock paths (`./apiBase`, `./parserService`) don't match the real import sources in `resourceService.ts`, adjust the `vi.mock` targets to the actual modules `getApiUrl`/`getAuthHeaders` are imported from (grep the top of `resourceService.ts`). The test asserts behavior, not import paths.

- [ ] **Step 3: Write the two functions**

In `frontend/services/resourceService.ts`, right after `uploadNewVersion` (before `setCurrentVersion`), add:

```ts
/**
 * Save edited text as a NEW version. Builds a File from the string and reuses
 * the existing multipart version-upload endpoint (content-addressed under
 * unified storage). Used by the text-resource editor's "Save as new version".
 */
export async function saveTextAsNewVersion(
  resourceId: string,
  text: string,
  filename: string,
  mime: string,
  notes?: string,
): Promise<ResourceVersion> {
  const file = new File([text], filename, { type: mime || 'text/plain' });
  return uploadNewVersion(resourceId, file, notes);
}

/**
 * OVERWRITE the current version's bytes in place (no new version row).
 * Used by the text-resource editor's "Overwrite current version".
 */
export async function overwriteVersionContent(
  resourceId: string,
  versionId: string,
  text: string,
  filename: string,
  mime: string,
): Promise<ResourceVersion> {
  const apiUrl = getApiUrl();
  const file = new File([text], filename, { type: mime || 'text/plain' });
  const formData = new FormData();
  formData.append('file', file);

  const headers: Record<string, string> = {};
  const authHeaders = await getAuthHeaders();
  Object.entries(authHeaders).forEach(([k, v]) => {
    if (k.toLowerCase() !== 'content-type') headers[k] = v as string;
  });

  const response = await fetch(
    `${apiUrl}/api/v1/resources/${resourceId}/versions/${versionId}/content`,
    { method: 'PUT', headers, body: formData },
  );
  if (!response.ok) throw new Error('Failed to overwrite version');
  const json = await response.json();
  return json.data;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run services/resourceService.textsave.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/services/resourceService.ts frontend/services/resourceService.textsave.test.ts
git commit -m "feat(resources): saveTextAsNewVersion + overwriteVersionContent service fns"
```

---

## Task 5: PlainTextResourceEditor component

**Files:**
- Create: `frontend/components/resources/PlainTextResourceEditor.tsx`
- Test: `frontend/components/resources/PlainTextResourceEditor.test.tsx`

**Interfaces:**
- Consumes: `plainTextToDoc` / `docToPlainText` (Task 2), `codeLangForExtension` (Task 1), `@tiptap/react` `useEditor`/`EditorContent`, `@tiptap/starter-kit` (its default code block is replaced), `@tiptap/extension-code-block-lowlight`, `lowlight`.
- Produces: `PlainTextResourceEditor({ value: string; ext: string; readOnly?: boolean; onChange?: (text: string) => void }): JSX.Element`. On every edit it calls `onChange` with the byte-exact current text (via `docToPlainText(editor.getJSON())`).

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/components/resources/PlainTextResourceEditor.test.tsx
// Real TipTap in jsdom — mirror NoteEditor.test.tsx's layout polyfills.
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { PlainTextResourceEditor } from './PlainTextResourceEditor';

const zeroRect = { bottom: 0, height: 0, left: 0, right: 0, toJSON: () => ({}), top: 0, width: 0, x: 0, y: 0 };
Element.prototype.getClientRects = () => [] as unknown as DOMRectList;
Element.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
if (!('Range' in globalThis)) {
  // jsdom provides Range; keep this guard harmless.
}

describe('PlainTextResourceEditor', () => {
  it('renders the initial text content into a code block', () => {
    const { container } = render(
      <PlainTextResourceEditor value={'PORT=8080\nHOST=localhost'} ext="env" />,
    );
    // The raw text lands verbatim in a <pre>/<code> (ProseMirror code block).
    expect(container.querySelector('pre')?.textContent).toBe('PORT=8080\nHOST=localhost');
  });

  it('is read-only when readOnly is set (no contenteditable=true)', () => {
    const { container } = render(
      <PlainTextResourceEditor value={'x'} ext="txt" readOnly />,
    );
    const editable = container.querySelector('[contenteditable="true"]');
    expect(editable).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/resources/PlainTextResourceEditor.test.tsx`
Expected: FAIL — cannot resolve `./PlainTextResourceEditor`.

- [ ] **Step 3: Write the component**

```tsx
// frontend/components/resources/PlainTextResourceEditor.tsx
// A single-code-block TipTap editor for non-markdown text resources
// (spec 2026-07-14). Byte-preserving: content is loaded via plainTextToDoc
// and read back via docToPlainText(getJSON()) — never through markdown.
// Optional lowlight syntax highlight by file extension.
import { useEffect } from 'react';
import { EditorContent, useEditor } from '@tiptap/react';
import Document from '@tiptap/extension-document';
import Text from '@tiptap/extension-text';
import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight';
import { common, createLowlight } from 'lowlight';
import { plainTextToDoc, docToPlainText } from '../../utils/tiptapPlainText';
import { codeLangForExtension } from '../../utils/textResourceMode';

const lowlight = createLowlight(common);

interface Props {
  value: string;
  ext: string;
  readOnly?: boolean;
  onChange?: (text: string) => void;
}

export function PlainTextResourceEditor({ value, ext, readOnly, onChange }: Props) {
  const language = codeLangForExtension(ext);
  const editor = useEditor({
    editable: !readOnly,
    // A doc that holds exactly one code block — no paragraphs, no marks, so
    // nothing can transform the bytes.
    extensions: [
      Document.extend({ content: 'codeBlock' }),
      Text,
      CodeBlockLowlight.configure({ lowlight }),
    ],
    content: plainTextToDoc(value, language),
    onUpdate: ({ editor: ed }) => {
      onChange?.(docToPlainText(ed.getJSON()));
    },
  });

  // Keep editability in sync if the prop flips.
  useEffect(() => {
    editor?.setEditable(!readOnly);
  }, [editor, readOnly]);

  if (!editor) return null;
  return (
    <div className="w-full max-h-[calc(100vh-13rem)] overflow-auto rounded-lg bg-island-2 text-sm">
      <EditorContent editor={editor} className="p-3 font-mono" />
    </div>
  );
}

export default PlainTextResourceEditor;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/resources/PlainTextResourceEditor.test.tsx`
Expected: PASS.

If the first test fails because the code block renders as `<pre><code>` and `textContent` includes an extra newline, adjust the assertion to `.toContain('PORT=8080')` and add a second assertion `expect(container.querySelector('pre')?.textContent).toContain('HOST=localhost')`. The byte-fidelity guarantee itself is already covered by Task 2's pure round-trip test; this component test only needs to prove the text mounts and readOnly is honored.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/resources/PlainTextResourceEditor.tsx frontend/components/resources/PlainTextResourceEditor.test.tsx
git commit -m "feat(resources): PlainTextResourceEditor (byte-preserving code-block tiptap)"
```

---

## Task 6: MarkdownResourceEditor component

**Files:**
- Create: `frontend/components/resources/MarkdownResourceEditor.tsx`
- Test: `frontend/components/resources/MarkdownResourceEditor.test.tsx`

**Interfaces:**
- Consumes: existing `NoteEditor` from `../Inspiration/NoteEditor` (props `value`, `onChange`, `placeholder`).
- Produces: `MarkdownResourceEditor({ value: string; readOnly?: boolean; onChange?: (markdown: string) => void; ext?: string }): JSX.Element`. In read-only mode it renders the markdown non-editably (reuse the existing read-only markdown renderer `NoteMarkdown` from `../Inspiration/NoteMarkdown`); in edit mode it renders `NoteEditor`. `ext` is accepted-but-ignored so the dispatcher (Task 7) can call both editors with the same prop shape.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/components/resources/MarkdownResourceEditor.test.tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

// Stub the heavy TipTap NoteEditor — this test only checks dispatch (edit vs
// read-only), not TipTap internals.
vi.mock('../Inspiration/NoteEditor', () => ({
  NoteEditor: ({ value }: { value: string }) => <div data-testid="note-editor">{value}</div>,
}));
vi.mock('../Inspiration/NoteMarkdown', () => ({
  NoteMarkdown: ({ source }: { source: string }) => <div data-testid="note-markdown">{source}</div>,
}));

import { MarkdownResourceEditor } from './MarkdownResourceEditor';

describe('MarkdownResourceEditor', () => {
  it('renders the read-only markdown renderer when readOnly', () => {
    render(<MarkdownResourceEditor value={'# Hi'} readOnly />);
    expect(screen.getByTestId('note-markdown')).toHaveTextContent('# Hi');
    expect(screen.queryByTestId('note-editor')).toBeNull();
  });
  it('renders the editor when not readOnly', () => {
    render(<MarkdownResourceEditor value={'# Hi'} onChange={() => {}} />);
    expect(screen.getByTestId('note-editor')).toHaveTextContent('# Hi');
    expect(screen.queryByTestId('note-markdown')).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/resources/MarkdownResourceEditor.test.tsx`
Expected: FAIL — cannot resolve `./MarkdownResourceEditor`.

Note: confirm `NoteMarkdown`'s prop name — grep `frontend/components/Inspiration/NoteMarkdown.tsx` for its Props interface. If the prop is not `source` (e.g. `markdown` or `content`), use the real name in BOTH the mock and the component below.

- [ ] **Step 3: Write the component**

```tsx
// frontend/components/resources/MarkdownResourceEditor.tsx
// Markdown text resources: reuse the platform's TipTap markdown editor
// (NoteEditor) for editing and the shared markdown renderer for read-only
// preview (spec 2026-07-14). WYSIWYG markdown is intentionally lossy on
// exact formatting — acceptable for content-first .md files.
import { NoteEditor } from '../Inspiration/NoteEditor';
import { NoteMarkdown } from '../Inspiration/NoteMarkdown';

interface Props {
  value: string;
  readOnly?: boolean;
  onChange?: (markdown: string) => void;
  // Accepted-but-ignored: lets the dispatcher call both editors with the same
  // prop shape (markdown has no per-language mode).
  ext?: string;
}

export function MarkdownResourceEditor({ value, readOnly, onChange }: Props) {
  if (readOnly) {
    return (
      <div className="w-full max-h-[calc(100vh-13rem)] overflow-auto px-1">
        <NoteMarkdown source={value} />
      </div>
    );
  }
  return (
    <div className="w-full max-h-[calc(100vh-13rem)] overflow-auto">
      <NoteEditor value={value} onChange={(md) => onChange?.(md)} minRows={12} />
    </div>
  );
}

export default MarkdownResourceEditor;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/resources/MarkdownResourceEditor.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/resources/MarkdownResourceEditor.tsx frontend/components/resources/MarkdownResourceEditor.test.tsx
git commit -m "feat(resources): MarkdownResourceEditor (NoteEditor edit + NoteMarkdown read-only)"
```

---

## Task 7: TextResourcePreview dispatcher

**Files:**
- Create: `frontend/components/resources/TextResourcePreview.tsx`
- Test: `frontend/components/resources/TextResourcePreview.test.tsx`

**Interfaces:**
- Consumes: `classifyTextResource` (Task 1), `saveTextAsNewVersion` + `overwriteVersionContent` + `fetchResourceVersions` (Tasks 4 / existing), `MarkdownResourceEditor` (Task 6), `PlainTextResourceEditor` (Task 5), `useToast` from `../Toast`, `useTranslation`.
- Produces: `TextResourcePreview({ resource: Resource; fileUrl: string; canEdit: boolean; onSaved?: () => void }): JSX.Element`. Fetches `fileUrl` as text on mount, classifies, renders the right editor read-only, exposes an "Edit" button (only when `canEdit`), and in edit mode a save bar with "Save as new version" + "Overwrite current version".

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/components/resources/TextResourcePreview.test.tsx
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('./MarkdownResourceEditor', () => ({
  MarkdownResourceEditor: ({ value, readOnly }: { value: string; readOnly?: boolean }) => (
    <div data-testid="md-editor" data-readonly={String(!!readOnly)}>{value}</div>
  ),
}));
vi.mock('./PlainTextResourceEditor', () => ({
  PlainTextResourceEditor: ({ value, readOnly }: { value: string; readOnly?: boolean }) => (
    <div data-testid="code-editor" data-readonly={String(!!readOnly)}>{value}</div>
  ),
}));
const saveNew = vi.fn(async () => ({ id: 'v2' }));
const overwrite = vi.fn(async () => ({ id: 'v1' }));
vi.mock('../../services/resourceService', () => ({
  saveTextAsNewVersion: (...a: unknown[]) => saveNew(...a),
  overwriteVersionContent: (...a: unknown[]) => overwrite(...a),
  fetchResourceVersions: async () => [{ id: 'v1', version_number: 1 }],
}));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }) }));

import { TextResourcePreview } from './TextResourcePreview';

const res = (over: Record<string, unknown> = {}) => ({
  id: '10', filename: 'notes.md', mime_type: 'text/markdown',
  file_size_bytes: 20, current_version: 1, ...over,
}) as never;

beforeEach(() => {
  saveNew.mockClear();
  overwrite.mockClear();
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => '# Hello' } as Response)));
});

describe('TextResourcePreview', () => {
  it('fetches text and renders the markdown editor read-only', async () => {
    render(<TextResourcePreview resource={res()} fileUrl="http://x/file" canEdit={false} />);
    await waitFor(() => expect(screen.getByTestId('md-editor')).toHaveTextContent('# Hello'));
    expect(screen.getByTestId('md-editor').getAttribute('data-readonly')).toBe('true');
    // No edit button when canEdit is false.
    expect(screen.queryByText('Edit')).toBeNull();
  });

  it('renders the code editor for a .json resource', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => '{"a":1}' } as Response)));
    render(<TextResourcePreview resource={res({ filename: 'c.json', mime_type: 'application/json' })} fileUrl="http://x/file" canEdit={false} />);
    await waitFor(() => expect(screen.getByTestId('code-editor')).toBeInTheDocument());
  });

  it('editor + save-as-new-version wiring', async () => {
    render(<TextResourcePreview resource={res()} fileUrl="http://x/file" canEdit onSaved={() => {}} />);
    await waitFor(() => screen.getByTestId('md-editor'));
    fireEvent.click(screen.getByText('Edit'));
    fireEvent.click(screen.getByText('Save as new version'));
    await waitFor(() => expect(saveNew).toHaveBeenCalledTimes(1));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/resources/TextResourcePreview.test.tsx`
Expected: FAIL — cannot resolve `./TextResourcePreview`.

- [ ] **Step 3: Write the component**

```tsx
// frontend/components/resources/TextResourcePreview.tsx
// Dispatcher for text-type resources on the detail page (spec 2026-07-14):
// fetch the raw text, classify it, render the right TipTap editor read-only,
// and (for the creator) offer edit + save-as-new-version / overwrite.
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Download, Pencil, Save, X } from 'lucide-react';
import type { Resource } from '../../types';
import { classifyTextResource, TEXT_EDIT_MAX_BYTES } from '../../utils/textResourceMode';
import {
  saveTextAsNewVersion,
  overwriteVersionContent,
  fetchResourceVersions,
} from '../../services/resourceService';
import { MarkdownResourceEditor } from './MarkdownResourceEditor';
import { PlainTextResourceEditor } from './PlainTextResourceEditor';
import { useToast } from '../Toast';

interface Props {
  resource: Resource;
  fileUrl: string;
  canEdit: boolean;
  onSaved?: () => void;
}

function extOf(filename: string | null): string {
  if (!filename) return '';
  const dot = filename.lastIndexOf('.');
  return dot >= 0 ? filename.slice(dot + 1).toLowerCase() : '';
}

export function TextResourcePreview({ resource, fileUrl, canEdit, onSaved }: Props) {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [text, setText] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mode = useMemo(
    () =>
      classifyTextResource({
        filename: resource.filename,
        mime: resource.mime_type,
        sizeBytes: resource.file_size_bytes ?? null,
      }),
    [resource.filename, resource.mime_type, resource.file_size_bytes],
  );
  const ext = extOf(resource.filename);

  useEffect(() => {
    let alive = true;
    // Oversize files never fetch the full body — the read-only fallback shows
    // a truncated slice; but we still fetch (Range) a 64 KB head. For v1 we
    // fetch the whole body only for editable sizes; oversize shows download.
    if (mode === 'oversize' || mode === null) {
      setText('');
      return;
    }
    (async () => {
      try {
        const resp = await fetch(fileUrl);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const body = await resp.text();
        if (alive) {
          setText(body);
          setDraft(body);
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [fileUrl, mode]);

  if (mode === 'oversize') {
    return (
      <div className="flex flex-col items-center gap-4 text-center">
        <p className="text-content-2 font-medium">{resource.filename}</p>
        <p className="text-content-3 text-sm">
          {t('resources.textTooLargeToEdit', 'File is too large to preview or edit inline.')}
        </p>
        <a
          href={fileUrl}
          download
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm"
        >
          <Download size={16} />
          {t('resources.download', 'Download')}
        </a>
      </div>
    );
  }

  if (error) {
    return <p className="text-content-3 text-sm">{t('resources.previewFailed', 'Failed to load preview')}: {error}</p>;
  }
  if (text === null) {
    return <p className="text-content-3 text-sm">{t('common.loading', 'Loading...')}</p>;
  }

  const Editor =
    mode === 'markdown' ? MarkdownResourceEditor : PlainTextResourceEditor;

  const runSave = async (kind: 'new' | 'overwrite') => {
    setSaving(true);
    try {
      const filename = resource.filename || 'file.txt';
      const mime = resource.mime_type || 'text/plain';
      if (kind === 'new') {
        await saveTextAsNewVersion(String(resource.id), draft, filename, mime);
      } else {
        const versions = await fetchResourceVersions(String(resource.id));
        const current =
          versions.find((v) => v.version_number === resource.current_version) ??
          versions[0];
        if (!current) throw new Error('no current version');
        await overwriteVersionContent(
          String(resource.id),
          String(current.id),
          draft,
          filename,
          mime,
        );
      }
      addToast(t('resources.saved', 'Saved'), 'success');
      setText(draft);
      setEditing(false);
      onSaved?.();
    } catch (e) {
      console.error('Failed to save text resource:', e);
      addToast(t('resources.saveFailed', 'Failed to save'), 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="w-full">
      <div className="flex items-center justify-end gap-2 mb-2">
        {!editing && canEdit && (
          <button
            type="button"
            onClick={() => {
              setDraft(text);
              setEditing(true);
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-island-2 text-content-2 text-xs hover:bg-line"
          >
            <Pencil size={14} />
            {t('resources.edit', 'Edit')}
          </button>
        )}
        {editing && (
          <>
            <button
              type="button"
              disabled={saving}
              onClick={() => runSave('new')}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs disabled:opacity-60"
            >
              <Save size={14} />
              {t('resources.saveAsNewVersion', 'Save as new version')}
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => runSave('overwrite')}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-island-2 text-content-2 text-xs hover:bg-line disabled:opacity-60"
            >
              {t('resources.overwriteCurrentVersion', 'Overwrite current version')}
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => {
                setDraft(text);
                setEditing(false);
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-content-3 text-xs hover:bg-line"
            >
              <X size={14} />
              {t('common.cancel', 'Cancel')}
            </button>
          </>
        )}
      </div>
      <Editor
        value={editing ? draft : text}
        ext={ext}
        readOnly={!editing}
        onChange={setDraft}
      />
    </div>
  );
}

export default TextResourcePreview;
```

Note: `MarkdownResourceEditor` (Task 6) already declares an optional `ext?` prop, so both editors share the same call shape — no extra edit needed here.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/resources/TextResourcePreview.test.tsx`
Expected: PASS (3 cases).

- [ ] **Step 5: Commit**

```bash
git add frontend/components/resources/TextResourcePreview.tsx frontend/components/resources/TextResourcePreview.test.tsx
git commit -m "feat(resources): TextResourcePreview dispatcher (fetch + classify + save)"
```

---

## Task 8: Wire TextResourcePreview into ResourceDetailPage

**Files:**
- Modify: `frontend/components/ResourceDetailPage.tsx` (`FilePreview`, ~lines 297-333)
- Test: `frontend/components/ResourceDetailPage.textpreview.test.tsx`

**Interfaces:**
- Consumes: `TextResourcePreview` (Task 7), `classifyTextResource` (Task 1). `FilePreview` already receives `resource` + `fileUrl`; add a `currentUserId?: string` prop. The parent `ResourceDetailPage` already holds `currentUserId` state (populated from `claims.sub` at ~line 947) and renders `<FilePreview>` at ~lines 1736 and 1740 — pass `currentUserId={currentUserId}` at both call sites. `canEdit` is computed inside `FilePreview` from `resource.source_type === 'upload' && resource.creator_id === currentUserId`. (`Resource.creator_id`, `Resource.source_type`, `Resource.current_version` all exist in `frontend/types.ts` — verified.)

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/components/ResourceDetailPage.textpreview.test.tsx
// Narrow test: FilePreview routes a text resource to TextResourcePreview
// instead of the "no preview" fallback. We import FilePreview via a tiny
// re-export to keep the test focused (see Step 3 note).
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('./resources/TextResourcePreview', () => ({
  TextResourcePreview: () => <div data-testid="text-preview" />,
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }) }));
vi.mock('./Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

import { FilePreview } from './ResourceDetailPage';

const res = (over: Record<string, unknown> = {}) => ({
  id: '10', filename: 'notes.md', mime_type: 'text/markdown',
  file_size_bytes: 20, source_type: 'upload', creator_id: 'u1', ...over,
}) as never;

describe('FilePreview text routing', () => {
  it('routes a markdown resource to TextResourcePreview', () => {
    render(<FilePreview resource={res()} fileUrl="http://x/file" currentUserId="u1" />);
    expect(screen.getByTestId('text-preview')).toBeInTheDocument();
  });
  it('still shows no-preview for an unknown binary', () => {
    render(<FilePreview resource={res({ filename: 'a.bin', mime_type: 'application/octet-stream' })} fileUrl="http://x/file" currentUserId="u1" />);
    expect(screen.queryByTestId('text-preview')).toBeNull();
  });
});
```

If `FilePreview` is not currently exported, Step 3 adds `export` to its declaration.

**Fallback if the import chain is too heavy:** `ResourceDetailPage.tsx` is ~2600 lines; importing `FilePreview` from it executes every one of its imports in jsdom, which may fail on a browser-only API. If Step 2/4 errors at import (not at assertion), extract `FilePreview` into its own file `frontend/components/resources/FilePreview.tsx` (move the component + its `getFileIcon`/`downloadTextFile` helpers, or import those from a shared util), have `ResourceDetailPage.tsx` import it from there, and point the test's `import { FilePreview }` at the new file. This is also the better structure (the component is self-contained and the host file is far over the size guideline). Do the extraction only if the in-place import fails.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/ResourceDetailPage.textpreview.test.tsx`
Expected: FAIL — `FilePreview` is not exported / text routing not present.

- [ ] **Step 3: Wire it in**

In `frontend/components/ResourceDetailPage.tsx`:

1. Add imports near the other component imports (`TextResourcePreview` is imported at line 79 in the same style as `VersionManagerModal`):

```tsx
import { TextResourcePreview } from './resources/TextResourcePreview';
import { classifyTextResource } from '../utils/textResourceMode';
```

2. Add `currentUserId?: string` to `FilePreview`'s props type, and export it. Change the declaration:

```tsx
export const FilePreview: React.FC<{
  resource: Resource;
  fileUrl: string | null;
  currentUserId?: string;
  onCoverUpdated?: (updated: Resource) => void;
}> = ({ resource, fileUrl, currentUserId, onCoverUpdated }) => {
```

3. Inside `FilePreview`, after the `const mime = resource.mime_type || '';` line (and after the `if (!fileUrl)` guard block), add the text-routing branch **before** the existing `if (mime.startsWith('video/'))` chain:

```tsx
  const textMode = classifyTextResource({
    filename: resource.filename,
    mime: resource.mime_type,
    sizeBytes: resource.file_size_bytes ?? null,
  });
  if (fileUrl && textMode !== null) {
    const canEdit =
      resource.source_type === 'upload' &&
      !!currentUserId &&
      String(resource.creator_id) === String(currentUserId);
    return (
      <TextResourcePreview
        resource={resource}
        fileUrl={fileUrl}
        canEdit={canEdit}
      />
    );
  }
```

4. Pass `currentUserId` at both `<FilePreview ... />` call sites (~lines 1736 and 1740):

```tsx
<FilePreview resource={resource} fileUrl={fileUrl} currentUserId={currentUserId} onCoverUpdated={setResource} />
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/ResourceDetailPage.textpreview.test.tsx`
Expected: PASS.

- [ ] **Step 5: Full front-end typecheck + touched-area tests**

Run:
```bash
cd frontend && npx tsc --noEmit 2>&1 | grep -E 'resources/|textResourceMode|tiptapPlainText|ResourceDetailPage' || echo "no new tsc errors in touched files"
npx vitest run utils/textResourceMode.test.ts utils/tiptapPlainText.test.ts services/resourceService.textsave.test.ts components/resources components/ResourceDetailPage.textpreview.test.tsx
```
Expected: no new tsc errors in the touched files; all listed suites PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/ResourceDetailPage.tsx frontend/components/ResourceDetailPage.textpreview.test.tsx
git commit -m "feat(resources): route text resources to in-place TipTap preview/editor"
```

---

## Task 9: i18n strings + full verification

**Files:**
- Modify: `frontend/public/locales/en.json`, `frontend/public/locales/zh.json`

**Interfaces:** none (copy only).

- [ ] **Step 1: Add the new i18n keys**

Add under the `resources` object in BOTH locale files (English values in en.json, Chinese in zh.json). Keys used by the new components:

```
resources.edit                     → "Edit" / "编辑"
resources.saveAsNewVersion         → "Save as new version" / "存为新版本"
resources.overwriteCurrentVersion  → "Overwrite current version" / "覆盖当前版本"
resources.saved                    → "Saved" / "已保存"
resources.saveFailed               → "Failed to save" / "保存失败"
resources.textTooLargeToEdit       → "File is too large to preview or edit inline." / "文件过大，无法内联预览或编辑。"
resources.previewFailed            → "Failed to load preview" / "预览加载失败"
```

(`resources.download` and `common.loading` / `common.cancel` already exist — verify with `grep -n '"download"\|"loading"\|"cancel"' frontend/public/locales/en.json`; add any that are missing.)

- [ ] **Step 2: Full backend + frontend verification**

Backend:
```bash
cd backend && mv .env /tmp/tre-env 2>/dev/null; uv run pytest -q --ignore=tests/e2e 2>&1 | tail -1; mv /tmp/tre-env .env 2>/dev/null
```
Expected: the pre-existing `test_228_personal_singleton` failure (integration, unrelated) may appear only if `.env` is present — with `.env` moved aside it should be all-pass except any known-flaky. New tests all pass.

Frontend:
```bash
cd frontend && npx vitest run 2>&1 | tail -3
```
Expected: no regressions; new suites green.

- [ ] **Step 3: Commit**

```bash
git add frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "chore(i18n): text resource editor strings"
```

---

## Notes for the implementer

- **The byte-fidelity guarantee lives in Task 2's pure round-trip test** — that is the safety net for non-markdown files. If you ever change how plain text loads into the code block, that test must still pass.
- **Markdown is deliberately lossy** on exact formatting (WYSIWYG). Do not try to make it byte-exact — that is out of scope and contradicts the design.
- **Real-TipTap tests in jsdom** need the `getClientRects`/`getBoundingClientRect` polyfills (copied from `NoteEditor.test.tsx`). Component tests that stub the editors (Tasks 6, 7, 8) don't need them.
- **Version-list refresh:** `TextResourcePreview` calls `onSaved`; wire that in a follow-up to re-fetch the resource + versions in `ResourceDetailPage` if the detail page doesn't already refresh via Realtime. For v1 the toast + local `setText(draft)` is enough; the version panel picks up new rows on its next open.
- **Overwrite orphans:** the previous object is intentionally left on disk (dedup-safe); no cleanup in this plan. A storage GC is separate (PR-5 of the storage epic).
