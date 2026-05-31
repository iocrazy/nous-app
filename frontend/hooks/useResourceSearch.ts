import { useEffect, useRef, useState } from 'react';
import { searchResources } from '../services/resourceSearchService';
import type { ResourceSearchResponse } from '../types';

const DEBOUNCE_MS = 150;

const EMPTY_RESPONSE: ResourceSearchResponse = {
  results: [],
  counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
  next_cursor: null,
};

export function useResourceSearch(query: string, kinds: string, teamId?: string) {
  const [data, setData] = useState<ResourceSearchResponse>(EMPTY_RESPONSE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const ctrlRef = useRef<AbortController | null>(null);
  const cacheRef = useRef<Map<string, ResourceSearchResponse>>(new Map());

  useEffect(() => {
    const key = `${query}|${kinds}|${teamId ?? ''}`;
    if (cacheRef.current.has(key)) {
      setData(cacheRef.current.get(key)!);
      return;
    }
    const timer = setTimeout(async () => {
      ctrlRef.current?.abort();
      const ctrl = new AbortController();
      ctrlRef.current = ctrl;
      setLoading(true);
      setError(null);
      try {
        const resp = await searchResources({ q: query, kinds, limit: 20, teamId, signal: ctrl.signal });
        if (ctrl.signal.aborted) return;
        cacheRef.current.set(key, resp);
        setData(resp);
      } catch (err) {
        if ((err as { name?: string }).name === 'AbortError') return;
        setError(err as Error);
      } finally {
        if (!ctrl.signal.aborted) setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, kinds, teamId]);

  return { data, loading, error };
}
