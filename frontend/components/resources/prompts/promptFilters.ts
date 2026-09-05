// frontend/components/resources/prompts/promptFilters.ts
//
// The Prompts shelf keeps its whole view state in the URL: a filtered shelf is
// a link you can send someone. Unknown values are dropped rather than trusted —
// a hand-edited `origin=weird` must read as "no origin filter", not as a filter
// the server will reject.
import { PROMPT_FORMS, PROMPT_ORIGINS, type PromptEntry, type PromptForm, type PromptOrigin } from '../../../services/promptsService';

export type PromptSort = 'recent' | 'title';
export interface PromptFilters {
  form: PromptForm | null;
  origin: PromptOrigin | null;
  projectId: string | null;
  sort: PromptSort;
  q: string;
}

export function parsePromptFilters(sp: URLSearchParams): PromptFilters {
  const form = sp.get('form');
  const origin = sp.get('origin');
  return {
    form: PROMPT_FORMS.includes(form as PromptForm) ? (form as PromptForm) : null,
    origin: PROMPT_ORIGINS.includes(origin as PromptOrigin) ? (origin as PromptOrigin) : null,
    projectId: sp.get('project') || null,
    sort: sp.get('sort') === 'title' ? 'title' : 'recent',
    q: sp.get('q') ?? '',
  };
}

export function serializePromptFilters(f: PromptFilters): URLSearchParams {
  const sp = new URLSearchParams();
  if (f.form) sp.set('form', f.form);
  if (f.origin) sp.set('origin', f.origin);
  if (f.projectId) sp.set('project', f.projectId);
  if (f.sort !== 'recent') sp.set('sort', f.sort);
  // Written untrimmed (ruling R12). The input is controlled by the value
  // parsed back out of the URL, so trimming here meant the box could never
  // hold a space: typing "a " round-tripped to "a" before the next
  // keystroke. Only the decision to write the key at all uses trim().
  if (f.q.trim()) sp.set('q', f.q);
  return sp;
}

/** 'recent' keeps the server's order (it already sorted by updated_at); only
 *  'title' re-sorts, and it copies rather than sorting the caller's array. */
export function sortEntries(items: PromptEntry[], sort: PromptSort): PromptEntry[] {
  if (sort !== 'title') return items;
  return [...items].sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: 'base' }));
}
