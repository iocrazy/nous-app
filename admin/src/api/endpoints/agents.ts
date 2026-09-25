import type { Schema } from '../../types/api'

/**
 * System-agent catalog (`/api/v1/admin/agents`). The page fetches these with
 * `fetch` directly; this module is only where their response types live.
 *
 * Every column is nullable on the wire: presets are seeded from files and
 * hand-editable, and the backend never rejects an odd row.
 */
export type CatalogAgent = Schema<'AdminCatalogAgentItem'>
export type CatalogAgentList = Schema<'AdminCatalogAgentList'>
/** `PUT /admin/agents/{slug}`: the whole refreshed row + override counts. */
export type CatalogAgentDetail = Schema<'AdminCatalogAgentDetail'>
