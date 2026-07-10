-- 355: Canvas trash (Infinite-Canvas parity G9).
--
-- Soft delete: DELETE /canvases/{id} now stamps deleted_at instead of
-- dropping the row; restore clears it; purge (trash-only) really deletes.
-- Every listing path filters deleted_at IS NULL at the repository layer.

ALTER TABLE canvases ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL;

-- Trash listings only scan deleted rows; live queries filter IS NULL and
-- already ride idx_canvases_project(_updated) — a partial index keeps the
-- trash path cheap without widening the hot ones.
CREATE INDEX IF NOT EXISTS idx_canvases_deleted
  ON canvases(deleted_at DESC)
  WHERE deleted_at IS NOT NULL;

COMMENT ON COLUMN canvases.deleted_at IS
  'Soft-delete stamp (G9 trash). NULL = live. Set by DELETE /canvases/{id}, cleared by restore, row removed for real by purge.';

-- PostgREST must see the new column immediately (CI apply skips a restart).
NOTIFY pgrst, 'reload schema';
