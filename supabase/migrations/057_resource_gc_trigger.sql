-- 057_resource_gc_trigger.sql
-- Garbage collection trigger: when the last resource_item referencing a
-- resource is deleted, automatically soft-delete (trash) the resource.

CREATE OR REPLACE FUNCTION check_orphan_resource()
RETURNS TRIGGER AS $$
BEGIN
  -- After a resource_item is deleted, check if the parent resource
  -- still has any remaining items referencing it.
  IF NOT EXISTS (
    SELECT 1 FROM resource_items WHERE resource_id = OLD.resource_id
  ) THEN
    -- No more references → soft-delete the resource
    UPDATE resources
    SET is_trashed = true,
        trashed_at = NOW()
    WHERE id = OLD.resource_id
      AND is_trashed = false;

    RAISE NOTICE 'Resource % marked as trashed (no remaining items)', OLD.resource_id;
  END IF;

  RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_check_orphan_resource
AFTER DELETE ON resource_items
FOR EACH ROW
EXECUTE FUNCTION check_orphan_resource();
