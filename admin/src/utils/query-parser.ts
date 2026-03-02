export interface ParsedFilter {
  field: string
  operator: 'eq' | 'prefix' | 'gt' | 'lt' | 'gte' | 'lte' | 'contains'
  value: string
  negate: boolean
}

export interface ParsedQuery {
  freeText: string
  filters: ParsedFilter[]
}

/**
 * Parse a KQL-like query string into structured filters.
 *
 * Supported syntax:
 * - `keyword` — free text search
 * - `field:value` — exact match
 * - `field:val*` — prefix match
 * - `field:>=N` — greater than or equal
 * - `field:<=N` — less than or equal
 * - `field:>N` — greater than
 * - `field:<N` — less than
 * - `AND` / `OR` — logical operators (AND is default)
 * - `NOT field:value` — negation
 */
export function parseQuery(input: string): ParsedQuery {
  const filters: ParsedFilter[] = []
  const freeTextParts: string[] = []

  const tokens = input
    .replace(/\bOR\b/g, 'AND')
    .split(/\bAND\b/)
    .map((t) => t.trim())
    .filter(Boolean)

  for (let token of tokens) {
    let negate = false
    if (token.startsWith('NOT ')) {
      negate = true
      token = token.slice(4).trim()
    }

    const colonIdx = token.indexOf(':')
    if (colonIdx === -1) {
      freeTextParts.push(token)
      continue
    }

    const field = token.slice(0, colonIdx).trim()
    let value = token.slice(colonIdx + 1).trim()

    let operator: ParsedFilter['operator'] = 'eq'
    if (value.startsWith('>=')) {
      operator = 'gte'
      value = value.slice(2)
    } else if (value.startsWith('<=')) {
      operator = 'lte'
      value = value.slice(2)
    } else if (value.startsWith('>')) {
      operator = 'gt'
      value = value.slice(1)
    } else if (value.startsWith('<')) {
      operator = 'lt'
      value = value.slice(1)
    } else if (value.endsWith('*')) {
      operator = 'prefix'
      value = value.slice(0, -1)
    }

    filters.push({ field, operator, value, negate })
  }

  return {
    freeText: freeTextParts.join(' '),
    filters,
  }
}

/**
 * Convert parsed query to API params for the search endpoint.
 */
export function queryToParams(parsed: ParsedQuery): Record<string, string> {
  const params: Record<string, string> = {}
  if (parsed.freeText) {
    params.q = parsed.freeText
  }
  if (parsed.filters.length > 0) {
    params.filters = JSON.stringify(parsed.filters)
  }
  return params
}
