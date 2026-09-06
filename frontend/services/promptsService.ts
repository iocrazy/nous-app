// frontend/services/promptsService.ts
//
// The unified prompt catalog (spec 2026-09-05 §3.1). ONE client for both
// surfaces — the resource library's Prompts page and the canvas Library panel.
// Nothing here talks to Supabase; the browser-side prompt query this replaces
// returned an empty list on RLS errors and is gone.
import { getApiUrl } from '../utils/apiConfig';
import { attachFile, createAsset, updateAsset } from './assetsService';
import { envelopeFetch } from './apiEnvelope';
import { getAuthHeaders } from './parserService';

export type PromptForm = 'template' | 'image' | 'album';
export type PromptOrigin = 'typed' | 'extracted' | 'captioned';
export type PromptSegment = 'mine' | 'project' | 'system';
export type PromptLang = 'en' | 'zh';
export const PROMPT_FORMS: readonly PromptForm[] = ['template', 'image', 'album'];
export const PROMPT_ORIGINS: readonly PromptOrigin[] = ['typed', 'extracted', 'captioned'];

export interface PromptThumb { url: string; kind: 'image' }
export interface PromptSlide {
  name: string;
  url: string | null;
  positive_en: string | null;
  positive_zh: string | null;
  negative_en: string | null;
  negative_zh: string | null;
}
export interface PromptTextSides {
  positive_en: string | null;
  positive_zh: string | null;
  negative_en: string | null;
  negative_zh: string | null;
}
export interface PromptEntry extends PromptTextSides {
  key: string;
  form: PromptForm;
  origin: PromptOrigin | null;
  title: string;
  tags: string[];
  params: Record<string, unknown> | null;
  thumbs: PromptThumb[];
  slides: PromptSlide[] | null;
  source: { store: 'assets' | 'uploads'; id: string };
  updated_at: string;
}
/**
 * One page of the catalog.
 *
 * ⚠️ Only `items` is narrowed by `form` / `origin` / `q` and paginated by
 * `limit` / `offset`. `total`, `by_form` and `by_origin` describe the WHOLE
 * unfiltered segment (ruling R6), so the facet counts stay put while the user
 * clicks through them — a count that shrank to match its own filter would say
 * "1 album" next to the album the filter just selected.
 */
export interface PromptPage {
  items: PromptEntry[];
  total: number;
  by_form: Record<PromptForm, number>;
  by_origin: Record<PromptOrigin, number>;
}
export interface PromptCounts { mine: number; project: number | null; system: number }

export interface FetchPromptsOptions {
  segment?: PromptSegment;
  projectId?: string | null;
  form?: PromptForm | null;
  origin?: PromptOrigin | null;
  q?: string;
  limit?: number;
  offset?: number;
}

const BASE = () => `${getApiUrl()}/api/v1/prompts`;

export async function fetchPrompts(scopeId: string, opts: FetchPromptsOptions = {}): Promise<PromptPage> {
  const qs = new URLSearchParams({ scope_id: scopeId, segment: opts.segment ?? 'mine' });
  if (opts.projectId) qs.set('project_id', opts.projectId);
  if (opts.form) qs.set('form', opts.form);
  if (opts.origin) qs.set('origin', opts.origin);
  const q = (opts.q ?? '').trim();
  if (q) qs.set('q', q);
  if (opts.limit !== undefined) qs.set('limit', String(opts.limit));
  if (opts.offset !== undefined) qs.set('offset', String(opts.offset));
  return envelopeFetch<PromptPage>(`${BASE()}?${qs}`, { headers: await getAuthHeaders() });
}

export async function fetchPromptCounts(scopeId: string, projectId?: string | null): Promise<PromptCounts> {
  const qs = new URLSearchParams({ scope_id: scopeId });
  if (projectId) qs.set('project_id', projectId);
  return envelopeFetch<PromptCounts>(`${BASE()}/counts?${qs}`, { headers: await getAuthHeaders() });
}

/** The text to show/insert for a language, falling back to the other side.
 *  `shownLang` says which side actually supplied the positive (null = none). */
export function promptText(src: PromptTextSides, lang: PromptLang): { positive: string; negative: string | null; shownLang: PromptLang | null } {
  const order: PromptLang[] = lang === 'zh' ? ['zh', 'en'] : ['en', 'zh'];
  const shownLang = order.find((l) => (l === 'en' ? src.positive_en : src.positive_zh)) ?? null;
  const positive = shownLang === 'en' ? src.positive_en! : shownLang === 'zh' ? src.positive_zh! : '';
  const negOrder = shownLang ? [shownLang, ...order.filter((l) => l !== shownLang)] : order;
  const negLang = negOrder.find((l) => (l === 'en' ? src.negative_en : src.negative_zh)) ?? null;
  const negative = negLang === 'en' ? src.negative_en : negLang === 'zh' ? src.negative_zh : null;
  return { positive, negative: negative ?? null, shownLang };
}

export function langAvailability(src: { positive_en: string | null; positive_zh: string | null }): 'both' | 'en' | 'zh' | 'none' {
  const en = !!src.positive_en, zh = !!src.positive_zh;
  return en && zh ? 'both' : en ? 'en' : zh ? 'zh' : 'none';
}

function num(v: unknown): number | null {
  const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN;
  return Number.isFinite(n) && n > 0 ? n : null;
}
function gcd(a: number, b: number): number { return b === 0 ? a : gcd(b, a % b); }

export function ratioFromParams(params: Record<string, unknown> | null): string | null {
  const w = num(params?.width), h = num(params?.height);
  if (!w || !h) return null;
  const g = gcd(Math.round(w), Math.round(h));
  return `${Math.round(w) / g}:${Math.round(h) / g}`;
}

export function paramChips(params: Record<string, unknown> | null): string[] {
  if (!params) return [];
  const out: string[] = [];
  const ratio = ratioFromParams(params);
  if (ratio) out.push(ratio);
  const w = num(params.width), h = num(params.height);
  if (w && h) out.push(`${w}×${h}`);
  if (num(params.steps)) out.push(`steps ${params.steps}`);
  if (num(params.cfg)) out.push(`cfg ${params.cfg}`);
  if (params.seed !== undefined && params.seed !== null && params.seed !== '') out.push(`seed ${params.seed}`);
  if (typeof params.sampler === 'string' && params.sampler) out.push(params.sampler);
  if (typeof params.model === 'string' && params.model) out.push(params.model);
  return out;
}

/** Relative catalog URL → absolute.
 *
 *  Deliberately NOT `features/canvas-core/smart/mediaUrl.ts::absolutize`: a
 *  resource-library service importing canvas code is the wrong direction, and
 *  the catalog is consumed by both surfaces. Nothing behaves differently — the
 *  only extra rule there is a cache-bust on `/api/v1/generated-media/{id}/cover`,
 *  and no catalog thumb has that shape (they are `/api/v1/resources/{id}/cover`
 *  and `/api/v1/media/{id}/slides/{name}`). Keep them apart rather than
 *  "unifying" and inheriting that rule by accident. */
/** Album slide files are served by `/api/v1/media/{id}/slides/{name}`, which
 *  is an authenticated endpoint: a bare `<img src>` cannot send a Bearer
 *  header, so — like SlidePlayer and every other slide `<img>` in the app —
 *  the URL carries the short-lived media token as `?token=`. Covers
 *  (`/resources/{id}/cover`) are public and need nothing. */
const SLIDE_URL_RE = /^\/api\/v1\/media\/[^/]+\/slides\//;

export function thumbSrc(url: string | null | undefined, mediaToken?: string | null): string {
  if (!url) return '';
  const absolute = url.startsWith('/api/') ? `${getApiUrl()}${url}` : url;
  if (!mediaToken || !SLIDE_URL_RE.test(url)) return absolute;
  const sep = absolute.includes('?') ? '&' : '?';
  return `${absolute}${sep}token=${encodeURIComponent(mediaToken)}`;
}

export interface SaveAsTemplateInput {
  title: string;
  group: string;
  positive: string;
  negative: string;
  positiveZh?: string;
  negativeZh?: string;
  exampleResourceIds: string[];
}

/** Route promoted text into the columns of the side it was actually read from.
 *
 *  A Chinese prompt promoted while the panel showed 中 belongs in
 *  `positive_zh` / `negative_zh`; written to the English columns it comes back
 *  labelled "EN only" with the 中 toggle greyed out, and nothing throws to say
 *  so. `side` is `promptText(...).shownLang` — the side that actually supplied
 *  the text — or null when the language is unknown (a canvas node's body),
 *  which stays EN. */
export function textForSide(side: PromptLang | null, positive: string, negative: string): Pick<SaveAsTemplateInput, 'positive' | 'negative' | 'positiveZh' | 'negativeZh'> {
  return side === 'zh'
    ? { positive: '', negative: '', positiveZh: positive, negativeZh: negative }
    : { positive, negative };
}

/** Spec §3.5: create the prompt asset, hang the pictures on its `examples`
 *  slot, make the first one the cover. Files do not move. Not rolled back on
 *  a partial failure — a visible half-made asset beats a silent one. */
export async function saveAsTemplate(scopeId: string, input: SaveAsTemplateInput): Promise<{ assetId: string }> {
  const group = input.group.trim();
  const created = await createAsset(scopeId, {
    asset_type: 'prompt',
    name: input.title.trim(),
    source: 'manual',
    prompt_positive: input.positive || null,
    prompt_negative: input.negative || null,
    prompt_positive_zh: input.positiveZh || null,
    prompt_negative_zh: input.negativeZh || null,
    tags: group ? { group: [group] } : {},
  });
  const assetId = String(created.id);
  for (const rid of input.exampleResourceIds) {
    await attachFile(scopeId, assetId, { resource_id: rid, slot: 'examples' });
  }
  if (input.exampleResourceIds.length > 0) {
    await updateAsset(scopeId, assetId, { cover_file_id: input.exampleResourceIds[0] });
  }
  return { assetId };
}
