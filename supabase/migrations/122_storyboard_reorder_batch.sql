-- ============================================================
-- Batch-update sort_order for storyboard frames in one statement
-- instead of N separate UPDATEs (N+1 fix).
-- ============================================================

CREATE OR REPLACE FUNCTION public.rpc_reorder_storyboard_frames(
    p_frame_ids UUID[]
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_updated INTEGER;
BEGIN
    UPDATE storyboard_frames AS sf
       SET sort_order = x.idx
      FROM unnest(p_frame_ids) WITH ORDINALITY AS x(frame_id, idx)
     WHERE sf.id = x.frame_id;

    GET DIAGNOSTICS v_updated = ROW_COUNT;
    RETURN v_updated;
END;
$$;

GRANT EXECUTE ON FUNCTION public.rpc_reorder_storyboard_frames(UUID[])
    TO service_role, authenticated;
