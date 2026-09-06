// features/canvas-core/library/usePromptCatalog.ts
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  fetchPromptCounts, fetchPrompts,
  type PromptCounts, type PromptForm, type PromptPage, type PromptSegment,
} from '../../../services/promptsService';

const DEBOUNCE_MS = 300;

export interface PromptCatalogState {
  page: PromptPage | null;
  counts: PromptCounts | null;
  loading: boolean;
  error: Error | null;
  reload: () => void;
}

export function usePromptCatalog(args: {
  scopeId: string;
  segment: PromptSegment;
  projectId: string | null;
  form: PromptForm | null;
  query: string;
  enabled: boolean;
}): PromptCatalogState {
  const { scopeId, segment, projectId, form, query, enabled } = args;
  const [page, setPage] = useState<PromptPage | null>(null);
  const [counts, setCounts] = useState<PromptCounts | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);
  const [debounced, setDebounced] = useState(query);
  const firstRef = useRef(true);

  useEffect(() => {
    if (firstRef.current) { firstRef.current = false; setDebounced(query); return; }
    const h = setTimeout(() => setDebounced(query), DEBOUNCE_MS);
    return () => clearTimeout(h);
  }, [query]);

  useEffect(() => {
    if (!enabled || !scopeId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPrompts(scopeId, { segment, projectId: segment === 'project' ? projectId : null, form, q: debounced, limit: 200 })
      .then((p) => { if (!cancelled) setPage(p); })
      .catch((err: unknown) => { console.error('[usePromptCatalog] load failed:', err); if (!cancelled) setError(err instanceof Error ? err : new Error(String(err))); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [enabled, scopeId, segment, projectId, form, debounced, tick]);

  useEffect(() => {
    if (!enabled || !scopeId) return;
    let cancelled = false;
    fetchPromptCounts(scopeId, projectId)
      .then((c) => { if (!cancelled) setCounts(c); })
      .catch((err: unknown) => console.error('[usePromptCatalog] counts failed:', err));
    return () => { cancelled = true; };
  }, [enabled, scopeId, projectId, tick]);

  const reload = useCallback(() => setTick((v) => v + 1), []);
  return { page, counts, loading, error, reload };
}
