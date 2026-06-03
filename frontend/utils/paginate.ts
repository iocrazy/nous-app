/**
 * Drain an offset-paginated endpoint into a single array.
 *
 * Calls `fetchPage(offset, pageSize)` repeatedly, accumulating rows until a
 * page returns fewer than `pageSize` items (the last page) or `maxItems` is
 * reached (safety cap against pathologically large sets). `capped` is true
 * only when the cap stopped us — callers should surface that so a truncated
 * list never silently reads as "everything".
 *
 * The Task Center needs the FULL set client-side: status/type/search filters
 * run in the browser, so a partial load would hide matching rows on unloaded
 * pages (the original limit=200 bug).
 */
export async function paginateAll<T>(
  fetchPage: (offset: number, pageSize: number) => Promise<T[]>,
  pageSize = 200,
  maxItems = 5000,
): Promise<{ items: T[]; capped: boolean }> {
  const items: T[] = [];
  for (let offset = 0; offset < maxItems; offset += pageSize) {
    const page = await fetchPage(offset, pageSize);
    items.push(...page);
    if (page.length < pageSize) return { items, capped: false };
  }
  return { items, capped: true };
}
