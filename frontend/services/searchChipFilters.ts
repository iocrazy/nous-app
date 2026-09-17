// frontend/services/searchChipFilters.ts
//
// One translation from "what the filter chips hold" to "what the search
// endpoint takes".
//
// My Downloads used to answer the same question two different ways: the list
// pushed the chips into `fetchLibraryPaginated` (server-side, every chip
// applied) while the search box called `/search/text` with no chips at all.
// Once a backend search returned, DownloadsView swapped the whole list for
// the hits, so every active chip stopped applying — silently, with the chips
// still drawn as active.
//
// The wire shape here is deliberately the SAME field names the library path
// already uses (`FetchLibraryFilterParams` → `rpc_downloads_library_search`'s
// `p_*` arguments → now `rpc_user_media_text_search` too), so a reader can
// follow one name end to end instead of learning a second vocabulary.

import type { FetchLibraryFilterParams } from './dataService';
import { mediaTypesToWire } from './dataService';

/** Wire shape of `TextSearchRequest.filters` (backend `LibraryChipFilters`). */
export interface SearchChipFilters {
  tag_ids?: string[];
  min_rating?: number;
  ai_transcribed?: boolean;
  ai_summarized?: boolean;
  ai_analyzed?: boolean;
  ai_has_prompt?: boolean;
  created_after?: string;
  created_before?: string;
  duration_min?: number;
  duration_max?: number;
  aspect_ratios?: string[];
  platforms?: string[];
  media_types?: string[];
  has_comments?: boolean;
  min_likes?: number;
  min_comments?: number;
  min_favorites?: number;
  min_shares?: number;
  social_combine?: 'and' | 'or';
}

/**
 * Translate the active chips for the search endpoint.
 *
 * Returns `undefined` when no chip is active, so a caller with an untouched
 * toolbar sends no `filters` key at all rather than an object full of
 * `undefined` — the request stays byte-identical to what it was before this
 * existed, which is what makes "nothing changed for users without filters" a
 * checkable claim.
 *
 * `media_types` goes through `mediaTypesToWire` — the same mapping the list
 * path uses, including its `__impossible__` sentinel for a type selection
 * that maps to no wire value (e.g. "document" in the web library). Dropping
 * the sentinel would turn "match nothing" into "match everything", which is
 * this whole file's failure mode in miniature.
 */
export const toSearchChipFilters = (
  f: FetchLibraryFilterParams | undefined,
): SearchChipFilters | undefined => {
  if (!f) return undefined;

  const mediaTypes = mediaTypesToWire(f.media_types);

  const out: SearchChipFilters = {};
  if (f.tag_ids && f.tag_ids.length > 0) out.tag_ids = f.tag_ids;
  if (f.min_rating) out.min_rating = f.min_rating;
  if (f.ai_transcribed) out.ai_transcribed = true;
  if (f.ai_summarized) out.ai_summarized = true;
  if (f.ai_analyzed) out.ai_analyzed = true;
  if (f.ai_has_prompt) out.ai_has_prompt = true;
  if (f.created_after) out.created_after = f.created_after;
  if (f.created_before) out.created_before = f.created_before;
  if (f.duration_min != null) out.duration_min = f.duration_min;
  if (f.duration_max != null) out.duration_max = f.duration_max;
  if (f.aspect_ratios && f.aspect_ratios.length > 0) {
    out.aspect_ratios = f.aspect_ratios;
  }
  if (f.platforms && f.platforms.length > 0) out.platforms = f.platforms;
  if (mediaTypes) out.media_types = mediaTypes;
  if (f.has_comments) out.has_comments = true;
  if (f.min_likes) out.min_likes = f.min_likes;
  if (f.min_comments) out.min_comments = f.min_comments;
  if (f.min_favorites) out.min_favorites = f.min_favorites;
  if (f.min_shares) out.min_shares = f.min_shares;
  // Only meaningful alongside a threshold; sending it alone would pin the
  // SQL CASE to a branch with nothing in it.
  if (
    f.social_combine &&
    (out.min_likes || out.min_comments || out.min_favorites || out.min_shares)
  ) {
    out.social_combine = f.social_combine;
  }

  return Object.keys(out).length > 0 ? out : undefined;
};
