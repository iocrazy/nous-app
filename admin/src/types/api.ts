/**
 * API shapes derived from the backend's Pydantic models (admin console).
 *
 * `api.generated.d.ts` is produced from `backend/openapi.json` by
 * `npm run gen:api` and is never edited by hand. This file is the only place
 * that reaches into it. Each `src/api/endpoints/<domain>.ts` module declares
 * its response types as aliases of `Schema<'…'>` — that module is already the
 * one place admin pages import a domain's types from, so a backend schema
 * rename still changes one line.
 *
 * Rules (spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md):
 * - A response shape is an alias of the generated schema, never a hand-written
 *   interface. Two copies of one shape may not coexist.
 * - The generated types are the real wire shape. Snowflake BIGINT ids that the
 *   backend returns as JSON numbers stay `number` — do not "fix" them here.
 * - Request bodies stay hand-written: openapi-typescript marks defaulted
 *   fields as required, which is right for responses and wrong for requests.
 */
import type { components } from './api.generated';

type Schemas = components['schemas'];

/** One generated response schema, by its OpenAPI component name. */
export type Schema<K extends keyof Schemas> = Schemas[K];

export type { components, paths } from './api.generated';
export type { Schemas };
