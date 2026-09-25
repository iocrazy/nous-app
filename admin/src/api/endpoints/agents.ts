import type { Schema } from '../../types/api'

/**
 * System-agent catalog (`/api/v1/admin/agents`) and the seed reload
 * (`/api/v1/ai-library/admin/reload-seeds`). The page fetches these with
 * `fetch` directly; this module is only where their response types live.
 *
 * Every column is nullable on the wire: presets are seeded from files and
 * hand-editable, and the backend never rejects an odd row.
 */
export type CatalogAgent = Schema<'AdminCatalogAgentItem'>
export type CatalogAgentList = Schema<'AdminCatalogAgentList'>
/** `PUT /admin/agents/{slug}`: the whole refreshed row + override counts. */
export type CatalogAgentDetail = Schema<'AdminCatalogAgentDetail'>
/** `POST /ai-library/admin/reload-seeds`: counts plus per-seed load errors. */
export type SeedReloadResult = Schema<'AdminSeedReloadResponse'>
