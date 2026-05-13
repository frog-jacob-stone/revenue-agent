import { useCallback, useEffect, useRef, useState } from 'react';

type CursorMode<T> = {
  kind: 'cursor';
  getCursor: (item: T) => number;
};

type OffsetMode = {
  kind: 'offset';
};

export interface UsePaginationOpts<T, P> {
  pageSize?: number;
  params: P;
  mode: CursorMode<T> | OffsetMode;
  fetcher: (args: {
    params: P;
    limit: number;
    cursor?: number;
    offset?: number;
  }) => Promise<T[]>;
}

export interface UsePaginationResult<T> {
  items: T[];
  loading: boolean;
  pageIndex: number;
  hasPrev: boolean;
  hasNext: boolean;
  goNext: () => void;
  goPrev: () => void;
  reset: () => void;
  refresh: () => void;
}

const DEFAULT_PAGE_SIZE = 25;

export function usePagination<T, P>(
  opts: UsePaginationOpts<T, P>,
): UsePaginationResult<T> {
  const { pageSize = DEFAULT_PAGE_SIZE, params, mode, fetcher } = opts;

  // Cursor stack: each entry is the cursor (id<cursor) used to fetch that page.
  // First page is `undefined`. Length-1 = current pageIndex. Only used in cursor mode.
  const [cursorStack, setCursorStack] = useState<(number | undefined)[]>([undefined]);
  // Offset mode tracks pageIndex directly.
  const [offsetPage, setOffsetPage] = useState(0);

  const [items, setItems] = useState<T[]>([]);
  const [hasNext, setHasNext] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);

  const paramsKey = JSON.stringify(params);
  // Refs let goNext/goPrev/refresh stay referentially stable while still seeing current state.
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const hasNextRef = useRef(hasNext);
  hasNextRef.current = hasNext;

  // Reset when filter params change.
  useEffect(() => {
    setCursorStack([undefined]);
    setOffsetPage(0);
  }, [paramsKey]);

  const pageIndex = mode.kind === 'cursor' ? cursorStack.length - 1 : offsetPage;
  const currentCursor = mode.kind === 'cursor' ? cursorStack[cursorStack.length - 1] : undefined;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const fetchArgs =
      mode.kind === 'cursor'
        ? { params, limit: pageSize + 1, cursor: currentCursor }
        : { params, limit: pageSize + 1, offset: offsetPage * pageSize };
    fetcher(fetchArgs)
      .then((result) => {
        if (cancelled) return;
        setHasNext(result.length > pageSize);
        setItems(result.slice(0, pageSize));
      })
      .catch((err) => {
        if (!cancelled) console.error(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paramsKey, currentCursor, offsetPage, pageSize, mode.kind, refreshKey]);

  const goNext = useCallback(() => {
    if (!hasNextRef.current || itemsRef.current.length === 0) return;
    if (mode.kind === 'cursor') {
      const last = itemsRef.current[itemsRef.current.length - 1];
      const nextCursor = mode.getCursor(last);
      setCursorStack((s) => [...s, nextCursor]);
    } else {
      setOffsetPage((p) => p + 1);
    }
  }, [mode]);

  const goPrev = useCallback(() => {
    if (mode.kind === 'cursor') {
      setCursorStack((s) => (s.length <= 1 ? s : s.slice(0, -1)));
    } else {
      setOffsetPage((p) => (p <= 0 ? 0 : p - 1));
    }
  }, [mode.kind]);

  const reset = useCallback(() => {
    if (mode.kind === 'cursor') setCursorStack([undefined]);
    else setOffsetPage(0);
  }, [mode.kind]);

  const refresh = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  return {
    items,
    loading,
    pageIndex,
    hasPrev: pageIndex > 0,
    hasNext,
    goNext,
    goPrev,
    reset,
    refresh,
  };
}
