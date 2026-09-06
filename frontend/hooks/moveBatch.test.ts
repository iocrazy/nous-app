/**
 * moveBatch.test.ts
 *
 * Batch Move used to hand every selected folder to `moveFolder`, which writes
 * `folders` through PostgREST directly and so never meets the folders
 * router's 409 `system_folder` guard (mig 441). A system folder in the
 * selection was therefore relocated with no error anywhere — and the caller
 * wrapped the whole batch in `catch { /* ignore *\/ }`, so even once mig 457
 * put the guard in the database the failure would have been swallowed.
 *
 * These two helpers are what makes that impossible: one refuses the locked
 * folders before anything moves, the other names the reason a folder mutation
 * was refused so the caller can echo it instead of discarding it. Rename goes
 * through the same PostgREST-direct write and reuses the second one.
 */

import { describe, it, expect } from 'vitest';

import { ApiError } from '../services/apiClient';
import type { Folder } from '../types';
import { partitionMovableFolders, describeFolderMutationFailure } from './moveBatch';

const folderRow = (over: Partial<Folder>): Folder => ({
  id: '742318905233408001',
  name: 'Folder',
  parent_id: null,
  library_id: null,
  scope_id: '742318905233407001',
  created_by: '00000000-0000-4000-8000-000000000001',
  sort_order: 0,
  is_system: false,
  system_key: null,
  icon: null,
  color: null,
  visibility: 'inherited',
  is_smart: false,
  smart_rules: null,
  is_trashed: false,
  trashed_at: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...over,
});

const PLAIN = folderRow({ id: '742318905233408004', name: 'Reference Boards' });
const CHAT_UPLOADS = folderRow({
  id: '742318905233408002',
  name: 'Chat Uploads',
  is_system: true,
  system_key: 'chat_uploads',
});
const COVER_TEMPLATES = folderRow({
  id: '742318905233408003',
  name: 'Cover Templates',
  is_system: true,
  system_key: 'cover_templates',
});

describe('partitionMovableFolders', () => {
  it('splits a mixed batch on is_system', () => {
    const { movable, locked } = partitionMovableFolders([
      PLAIN,
      CHAT_UPLOADS,
      COVER_TEMPLATES,
    ]);
    expect(movable.map((f) => f.id)).toEqual([PLAIN.id]);
    expect(locked.map((f) => f.id)).toEqual([CHAT_UPLOADS.id, COVER_TEMPLATES.id]);
  });

  it('leaves an all-plain batch whole — the guard must not cost normal moves', () => {
    const { movable, locked } = partitionMovableFolders([PLAIN]);
    expect(movable).toEqual([PLAIN]);
    expect(locked).toEqual([]);
  });

  it('reports every folder locked when they all are', () => {
    const { movable, locked } = partitionMovableFolders([CHAT_UPLOADS, COVER_TEMPLATES]);
    expect(movable).toEqual([]);
    expect(locked).toHaveLength(2);
  });

  it('handles an empty batch', () => {
    expect(partitionMovableFolders([])).toEqual({ movable: [], locked: [] });
  });
});

describe('describeFolderMutationFailure', () => {
  it('names the API layer 409 (mig 441 folders router)', () => {
    const err = new ApiError('System folder cannot be moved', 409, {
      code: 'system_folder',
    });
    expect(describeFolderMutationFailure(err)).toBe('system_folder');
  });

  it('names the DB trigger error PostgREST relays (mig 457)', () => {
    // Real PostgREST wire shape for `RAISE EXCEPTION 'system_folder' USING
    // ERRCODE = 'check_violation', HINT = 'system_folder'`.
    const err = {
      code: '23514',
      message: 'system_folder',
      details:
        'folder 742318905233408002 (chat_uploads) is a system folder and cannot be renamed, moved or trashed',
      hint: 'system_folder',
    };
    expect(describeFolderMutationFailure(err)).toBe('system_folder');
  });

  it('falls back to unknown for an unrelated failure', () => {
    expect(describeFolderMutationFailure(new Error('boom'))).toBe('unknown');
  });

  it('falls back to unknown for a different PostgREST error', () => {
    const err = { code: '23503', message: 'violates foreign key constraint', hint: null };
    expect(describeFolderMutationFailure(err)).toBe('unknown');
  });

  // The marker is ours and a real refusal always carries it whole. Matching it
  // as a substring would let an unrelated failure be reported to the user as a
  // system-folder refusal — confidently, and wrongly.
  it('does not match a message that merely contains the marker', () => {
    const err = { code: 'PGRST301', message: 'unrelated system_folder_report failure' };
    expect(describeFolderMutationFailure(err)).toBe('unknown');
  });

  it('falls back to unknown for a non-object throw', () => {
    expect(describeFolderMutationFailure(null)).toBe('unknown');
    expect(describeFolderMutationFailure('system_folder')).toBe('unknown');
  });
});
