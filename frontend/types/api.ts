/**
 * API shapes derived from the backend's Pydantic models.
 *
 * `api.generated.d.ts` is produced from `backend/openapi.json` by
 * `npm run gen:api` and is never edited by hand. This file is the only place
 * that reaches into it: business code imports the named aliases below, so a
 * backend schema rename changes one line here instead of every call site.
 *
 * Rules (spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md):
 * - An API shape lives here as an alias of the generated schema, never as a
 *   hand-written interface in `types.ts`. Two copies of one shape may not
 *   coexist.
 * - The generated types are the real wire shape. Snowflake BIGINT ids that
 *   the backend returns as JSON numbers stay `number` — do not "fix" them to
 *   `string` here (see CLAUDE.md, "边界 mock 必须用真实 JSON 形状").
 */
import type { components } from './api.generated';

type Schemas = components['schemas'];

/**
 * The `{ success, data }` envelope that most un-typed endpoints return.
 * Replaced by the generated `Envelope[T]` once the backend declares it.
 */
export interface Envelope<T> {
  success: boolean;
  data: T;
}

export type { components, paths } from './api.generated';
export type { Schemas };
