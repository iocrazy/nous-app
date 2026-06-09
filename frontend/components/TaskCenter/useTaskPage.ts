/**
 * Page-number (offset) pagination for the Settings → Tasks list.
 *
 * Owns the server-side query state — page, multi-select status/type filters,
 * debounced search, sort — and fetches ONE page + the total match count via
 * `fetchTasksPage`. Filter/sort/search changes reset to page 1. A bumped
 * `revision` (the context's Realtime signal) refetches the current page so a
 * running task's progress stays live without a full reload.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  fetchTasksPage,
  fetchMatchingTaskIds,
  type TaskSort,
  type UnifiedTask,
  type TaskStatus,
  type TaskType,
} from '../../contexts/TaskManagerContext';

export const TASK_PAGE_SIZE = 50;
/** Page-size choices (backend caps `limit` at 200). */
export const TASK_PAGE_SIZE_OPTIONS = [25, 50, 100, 200] as const;

export interface UseTaskPage {
  tasks: UnifiedTask[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
  loading: boolean;
  error: string | null;
  statuses: Set<TaskStatus>;
  types: Set<TaskType>;
  search: string;
  sort: TaskSort;
  setPage: (p: number) => void;
  setPageSize: (n: number) => void;
  toggleStatus: (s: TaskStatus) => void;
  toggleType: (t: TaskType) => void;
  setSearch: (q: string) => void;
  setSort: (s: TaskSort) => void;
  refresh: () => void;
  /** All terminal task ids matching the current filter (cross-page select-all). */
  fetchAllMatchingIds: () => Promise<{ ids: string[]; capped: boolean }>;
}

export function useTaskPage(revision: number): UseTaskPage {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSizeRaw] = useState<number>(TASK_PAGE_SIZE);
  const [statuses, setStatuses] = useState<Set<TaskStatus>>(new Set());
  const [types, setTypes] = useState<Set<TaskType>>(new Set());
  const [search, setSearchRaw] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [sort, setSortRaw] = useState<TaskSort>('created_desc');

  const [tasks, setTasks] = useState<UnifiedTask[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Debounce the search term (the page fetch keys off debouncedSearch).
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  // Guard against out-of-order responses (a fast filter change racing a slow
  // page fetch): only the latest request may commit.
  const reqId = useRef(0);
  const load = useCallback(async () => {
    const myId = ++reqId.current;
    setLoading(true);
    setError(null);
    try {
      const res = await fetchTasksPage({
        page,
        pageSize,
        statuses: [...statuses],
        types: [...types],
        search: debouncedSearch,
        sort,
      });
      if (myId !== reqId.current) return;
      setTasks(res.tasks);
      setTotal(res.total);
    } catch (e) {
      if (myId !== reqId.current) return;
      console.error('[useTaskPage] load failed:', e);
      setError(e instanceof Error ? e.message : 'Failed to load tasks');
    } finally {
      if (myId === reqId.current) setLoading(false);
    }
  }, [page, pageSize, statuses, types, debouncedSearch, sort]);

  useEffect(() => {
    load();
  }, [load]);

  // Realtime: refetch the current page when the context signals a change.
  // Debounced so a burst of DBOS UPDATEs coalesces into one refetch.
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    if (revision === 0) return;
    const timer = setTimeout(() => loadRef.current(), 400);
    return () => clearTimeout(timer);
  }, [revision]);

  // Filter/sort/search setters reset to page 1 (the result set changed).
  const toggleStatus = (s: TaskStatus) => {
    setStatuses((prev) => {
      const n = new Set(prev);
      n.has(s) ? n.delete(s) : n.add(s);
      return n;
    });
    setPage(1);
  };
  const toggleType = (t: TaskType) => {
    setTypes((prev) => {
      const n = new Set(prev);
      n.has(t) ? n.delete(t) : n.add(t);
      return n;
    });
    setPage(1);
  };
  const setSearch = (q: string) => {
    setSearchRaw(q);
    setPage(1);
  };
  const setSort = (s: TaskSort) => {
    setSortRaw(s);
    setPage(1);
  };
  const setPageSize = (n: number) => {
    setPageSizeRaw(n);
    setPage(1); // page index is meaningless across a size change
  };

  const fetchAllMatchingIds = useCallback(
    async () => {
      const r = await fetchMatchingTaskIds({
        statuses: [...statuses],
        types: [...types],
        search: debouncedSearch,
      });
      return { ids: r.ids, capped: r.capped };
    },
    [statuses, types, debouncedSearch],
  );

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return {
    tasks,
    total,
    page,
    pageSize,
    totalPages,
    loading,
    error,
    statuses,
    types,
    search,
    sort,
    setPage,
    setPageSize,
    toggleStatus,
    toggleType,
    setSearch,
    setSort,
    refresh: load,
    fetchAllMatchingIds,
  };
}
