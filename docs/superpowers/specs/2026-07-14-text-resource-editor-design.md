# Text Resource Preview + Editing — Design

**Date:** 2026-07-14
**Status:** Approved (design), pending implementation plan

## Problem

Text-type resources in the upload library (`.md`, `.txt`, `.env`, `.json`,
`.log`, code, …) currently fall into the resource detail page's default
"暂不支持预览" branch — an icon plus a download button. You can't read or edit
them in place. The user asked to reuse the platform's existing global TipTap
engine to add preview + editing for these files.

## Scope decision (why not a code editor)

This is a media/content platform, **not a code-editing platform**. The primary
real use case is editing **markdown** — prompt libraries, notes, docs. Other
text files (`.env`, `.json`, `.log`) only need to be **viewed and lightly
edited**, not developed in an IDE.

A full code editor (CodeMirror 6 / Monaco) was considered and **rejected** as
over-investment for a non-code platform: it would add a heavy dependency and a
second editor paradigm to serve a secondary need. The chosen approach is
Notion-flavored — one editor engine (TipTap), with a byte-preserving plain/code
mode for non-markdown text. (If code-editing ever becomes a first-class need,
CodeMirror 6 is the documented upgrade target; nothing here blocks that.)

## Approach: one engine (TipTap), two modes

| File | Mode | Notes |
|---|---|---|
| `.md` / `.markdown` / `text/markdown` | **Markdown WYSIWYG** | Reuse `Inspiration/NoteEditor` (already `@tiptap/markdown` round-trip). The main feature. |
| Other `text/*` + `.env` `.json` `.log` `.txt` `.csv` `.yaml` code | **Raw code-block (byte-preserving)** | Content loads into a single TipTap `codeBlock` node; load/save via `editor.getText()` — **never** through markdown serialization, so bytes round-trip exactly. Optional `lowlight` syntax highlighting (dependency already present). |
| Any text > **512 KB** | **Read-only fallback** | Show a truncated read-only preview (first ~64 KB) + a "download full file" button; neither editor mounts the full content (ProseMirror is size-sensitive; library text files are far below this). |

### Byte-fidelity contract (the one real risk)

- **Non-markdown text MUST NOT pass through the markdown parser/serializer.**
  It is loaded verbatim into a code-block node and saved via `getText()`. A test
  pins round-trip byte equality for a representative `.env`/`.json` sample.
- **Markdown WYSIWYG is intentionally lossy on exact formatting** (whitespace,
  hand-authored HTML, list-marker style may normalize on save). Acceptable for
  content-first markdown (prompt libraries); documented so it is not a surprise.

## Loading content

The detail page already computes a `fileUrl` (served by `/media/{id}` or
`/resources/{id}/file`, both now sb://-aware after the media-read-gaps fix). The
text preview `fetch`es that URL for raw text and feeds it to the editor.

## Editing + saving

### Permission
Reuse the existing `verify_resource_write_access` guard — **creator-only**,
mirroring the `resources` RLS "Creators can update resources". No new permission
model. The "edit" affordance is hidden for non-creators.

### View ↔ edit
The preview renders read-only by default. An "Edit" button (creator-only) enters
edit mode. This keeps the common case (viewing) light and avoids accidental
edits.

### Two save actions
- **Save as new version** (default, safe): reuse `POST /{id}/versions`. New
  content-addressed object + new `resource_versions` row; history preserved and
  rollback available via the existing `VersionManagerModal`. No orphaned object.
- **Overwrite current version** (secondary): a new small endpoint
  `PUT /{id}/versions/{version_id}/content` writes the new object and updates the
  current version row's `file_path` / `file_size_bytes` / `file_hash` in place —
  no new version row. The previous object may become orphaned; that is harmless
  and dedup-safe under content addressing (a future GC reclaims it). Guarded by
  the same creator-only check.

After a save, the resource + version list re-fetch so the panel reflects the new
current version.

## Components (small, focused)

Frontend:
- `TextResourcePreview.tsx` — dispatcher: picks markdown / plain-code / oversize
  by mime + extension + byte size. Owns the read/edit toggle and save-action bar.
- `MarkdownResourceEditor.tsx` — thin wrapper over `NoteEditor` (markdown in /
  out) plus the save bar.
- `PlainTextResourceEditor.tsx` — thin wrapper over a single-code-block TipTap
  configured for byte-preserving load/save, optional `lowlight` highlight by
  extension, plus the save bar.
- Wire the dispatcher into `ResourceDetailPage::FilePreview` in place of the
  current text→"暂不支持预览" fall-through.

Backend:
- `resources_versions_router` gains `PUT /{resource_id}/versions/{version_id}/content`
  (overwrite-current), guarded by `verify_resource_write_access`, going through
  the same content-addressed store path (`store_local_file`) the upload path uses.

Service/repo:
- Overwrite writes the new object via the existing storage helper and updates the
  version row (`file_path`, `file_size_bytes`, `file_hash`) — no direct
  phase/status columns touched (route-C compliance not relevant here; these are
  business columns on `resource_versions`).

## Extension → mode mapping (single source of truth)

A small lookup table (frontend) classifies by extension first, mime second:
- markdown: `md`, `markdown` (or `text/markdown`)
- everything else `text/*` or a known code/config extension → plain-code mode
- unknown extension that is still text → plain-code mode (byte-safe fallback,
  no highlight)
- non-text mime (binary) → unchanged (existing image/video/audio/pdf branches or
  the download fallback)

## Testing

Frontend:
- Dispatcher picks the right mode for representative mime/extension/size inputs
  (md → markdown; json/env → plain-code; >512KB → read-only fallback).
- Save-as-new-version calls `POST /{id}/versions`; overwrite calls the new
  content endpoint.
- Read-only state (non-creator, or view mode) shows no edit affordance.
- Byte-fidelity: a `.json`/`.env` sample loaded into plain-code mode and saved
  returns byte-identical content (no markdown transform).

Backend:
- Overwrite endpoint: creator passes → writes new object + updates version row;
  non-creator → 403; unknown resource/version → 404.
- New-version path already covered by existing `resources_versions_router` tests;
  add a text-content case.

## Out of scope (YAGNI)

- CodeMirror / Monaco / language services.
- CSV table rendering (a separate feature).
- Collaborative / real-time editing.
- Diff view between text versions (the `VersionManagerModal` set-current +
  download already covers comparison needs for v1).
- Windowed/virtualized editing for multi-MB text (handled by the read-only
  fallback instead).
