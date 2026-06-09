-- 276_smart_folder_search_rpc.sql
--
-- Purpose: server-side, keyset-paginated smart-folder evaluation. Replaces the
-- drain-all `ResourcesRepository.execute_smart_rules` path (PostgREST query +
-- Python tag post-filter + fetch-all-subtract for exclude), which silently caps
-- at PostgREST's 1000-row ceiling once a smart folder matches >1000 resources.
--
-- The whole rule predicate (resource-field conditions + tag EXISTS + AND/OR +
-- match/exclude) is built server-side from the JSONB rules, so the result can
-- be keyset-paginated AND searched without leaving SQL.
--
-- Returns a single jsonb: { "rows": [ ...up to p_limit keyset-ordered items... ],
--   "total_count": <bigint when p_with_count else null> }
-- Row shape matches the legacy path: to_jsonb(resource_items) || { resource: resources }.
--
-- Ordering / keyset cursor: ORDER BY ri.created_at DESC, ri.id DESC; cursor is
-- the (created_at, id) of the last row of the previous page.
--
-- SAFETY: the condition `field` is mapped through a FIXED allowlist to a
-- (column, type) pair — never interpolated raw — and VALUES go through
-- format(%L) literal-quoting. Unknown fields/ops are skipped (parity with the
-- legacy code, which `return query` / `return None` on unknown ops). Relative
-- dates (`relative:-7d`) are resolved to absolute by the CALLER before this RPC.

CREATE OR REPLACE FUNCTION public.search_smart_folder(
  p_scope_id    text,
  p_rules       jsonb,
  p_search      text        DEFAULT NULL,
  p_cursor_ts   timestamptz DEFAULT NULL,
  p_cursor_id   bigint      DEFAULT NULL,
  p_limit       int         DEFAULT 40,
  p_with_count  bool        DEFAULT false
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  v_operator  text  := upper(coalesce(p_rules->>'operator', 'AND'));
  v_match     bool  := coalesce((p_rules->>'match')::bool, true);
  v_combiner  text  := CASE WHEN v_operator = 'OR' THEN ' OR ' ELSE ' AND ' END;
  v_cond      jsonb;
  v_field     text;
  v_op        text;
  v_value     text;
  v_col       text;
  v_type      text;
  v_pred      text;
  v_res_preds text[] := '{}';   -- resource-field conditions
  v_tag_preds text[] := '{}';   -- tag membership conditions
  v_groups    text[] := '{}';
  v_rule      text;
  v_where     text;   -- rule + search (no cursor) — used by the count
  v_sql       text;
  v_rows      jsonb;
  v_total     bigint := NULL;
BEGIN
  FOR v_cond IN SELECT * FROM jsonb_array_elements(coalesce(p_rules->'conditions', '[]'::jsonb))
  LOOP
    v_field := v_cond->>'field';
    v_op    := v_cond->>'op';
    v_value := v_cond->>'value';
    v_pred  := NULL;

    IF v_field = 'tags' THEN
      -- Case-insensitive tag membership (mirrors _filter_by_tags).
      IF v_op = 'contains' THEN
        v_pred := format(
          'EXISTS (SELECT 1 FROM resource_tags rt JOIN tags t ON t.id = rt.tag_id'
          || ' WHERE rt.resource_id = r.id AND lower(t.name) = lower(%L))', v_value);
      ELSIF v_op = 'not_contains' THEN
        v_pred := format(
          'NOT EXISTS (SELECT 1 FROM resource_tags rt JOIN tags t ON t.id = rt.tag_id'
          || ' WHERE rt.resource_id = r.id AND lower(t.name) = lower(%L))', v_value);
      END IF;
      IF v_pred IS NOT NULL THEN
        v_tag_preds := array_append(v_tag_preds, '(' || v_pred || ')');
      END IF;
    ELSE
      -- Allowlist: field → (column, cast-type). Unknown field → skip (parity).
      v_col := CASE v_field
        WHEN 'filename'         THEN 'r.filename'
        WHEN 'file_type'        THEN 'r.file_type'
        WHEN 'source_type'      THEN 'r.source_type'
        WHEN 'mime_type'        THEN 'r.mime_type'
        WHEN 'resolution'       THEN 'r.resolution'
        WHEN 'file_size_bytes'  THEN 'r.file_size_bytes'
        WHEN 'duration_seconds' THEN 'r.duration_seconds'
        WHEN 'created_at'       THEN 'r.created_at'
        ELSE NULL END;
      v_type := CASE v_field
        WHEN 'file_size_bytes'  THEN 'bigint'
        WHEN 'duration_seconds' THEN 'integer'
        WHEN 'created_at'       THEN 'timestamptz'
        ELSE 'text' END;

      IF v_col IS NOT NULL THEN
        v_pred := CASE v_op
          WHEN 'eq'          THEN format('%s = %L::%s', v_col, v_value, v_type)
          WHEN 'contains'    THEN format('%s ILIKE %L', v_col, '%' || v_value || '%')
          WHEN 'starts_with' THEN format('%s ILIKE %L', v_col, v_value || '%')
          WHEN 'gt'          THEN format('%s > %L::%s',  v_col, v_value, v_type)
          WHEN 'lt'          THEN format('%s < %L::%s',  v_col, v_value, v_type)
          WHEN 'gte'         THEN format('%s >= %L::%s', v_col, v_value, v_type)
          WHEN 'lte'         THEN format('%s <= %L::%s', v_col, v_value, v_type)
          WHEN 'in'          THEN format('%s::text = ANY(string_to_array(%L, %L))', v_col, v_value, ',')
          ELSE NULL END;
      END IF;
      IF v_pred IS NOT NULL THEN
        v_res_preds := array_append(v_res_preds, '(' || v_pred || ')');
      END IF;
    END IF;
  END LOOP;

  -- PARITY with execute_smart_rules: the legacy path combines resource
  -- conditions among themselves by `operator`, then *post-filters* the result
  -- by tag conditions (also combined by `operator`). So the resource-group and
  -- tag-group are ALWAYS intersected (AND), even when operator='OR'. Replicate
  -- that exactly: each group is OR/AND internally, the two groups are AND-ed.
  IF array_length(v_res_preds, 1) IS NOT NULL THEN
    v_groups := array_append(v_groups, '(' || array_to_string(v_res_preds, v_combiner) || ')');
  END IF;
  IF array_length(v_tag_preds, 1) IS NOT NULL THEN
    v_groups := array_append(v_groups, '(' || array_to_string(v_tag_preds, v_combiner) || ')');
  END IF;

  -- No valid conditions → match nothing (the endpoint already guards the
  -- empty-conditions case, so this is only hit by all-unknown rules).
  IF array_length(v_groups, 1) IS NULL THEN
    v_rule := 'false';
  ELSE
    v_rule := array_to_string(v_groups, ' AND ');
  END IF;

  -- Exclude mode: negate the whole matched predicate (replaces fetch-all-subtract).
  IF NOT v_match THEN
    v_rule := 'NOT (' || v_rule || ')';
  END IF;

  v_where := format('ri.scope_id = %L::bigint AND r.is_trashed = false AND (%s)',
                    p_scope_id, v_rule);

  IF p_search IS NOT NULL AND length(btrim(p_search)) > 0 THEN
    v_where := v_where || format(
      ' AND (r.filename ILIKE %L OR r.url ILIKE %L OR r.notes ILIKE %L)',
      '%' || p_search || '%', '%' || p_search || '%', '%' || p_search || '%');
  END IF;

  IF p_with_count THEN
    v_sql := format(
      'SELECT count(*) FROM resource_items ri JOIN resources r ON r.id = ri.resource_id WHERE %s',
      v_where);
    EXECUTE v_sql INTO v_total;
  END IF;

  -- Keyset cursor on (ri.created_at, ri.id) for the ROW page only.
  IF p_cursor_ts IS NOT NULL AND p_cursor_id IS NOT NULL THEN
    v_where := v_where || format(
      ' AND (ri.created_at < %L::timestamptz OR (ri.created_at = %L::timestamptz AND ri.id < %L::bigint))',
      p_cursor_ts, p_cursor_ts, p_cursor_id);
  END IF;

  v_sql := format(
    'SELECT coalesce(jsonb_agg(row ORDER BY ord_ts DESC, ord_id DESC), ''[]''::jsonb)'
    || ' FROM ('
    || '   SELECT ri.created_at AS ord_ts, ri.id AS ord_id,'
    || '          to_jsonb(ri.*) || jsonb_build_object(''resource'', to_jsonb(r.*)) AS row'
    || '   FROM resource_items ri JOIN resources r ON r.id = ri.resource_id'
    || '   WHERE %s'
    || '   ORDER BY ri.created_at DESC, ri.id DESC'
    || '   LIMIT %s'
    || ' ) s', v_where, p_limit);
  EXECUTE v_sql INTO v_rows;

  RETURN jsonb_build_object('rows', coalesce(v_rows, '[]'::jsonb), 'total_count', v_total);
END;
$$;

GRANT EXECUTE ON FUNCTION public.search_smart_folder(
  text, jsonb, text, timestamptz, bigint, int, bool
) TO authenticated, service_role;
