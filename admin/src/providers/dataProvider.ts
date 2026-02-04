import { DataProvider } from '@refinedev/core'
import { supabase } from '../lib/supabase'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080'

async function getAuthHeaders(): Promise<Record<string, string>> {
  const { data: { session } } = await supabase.auth.getSession()

  if (session?.access_token) {
    return {
      'Authorization': `Bearer ${session.access_token}`,
      'Content-Type': 'application/json',
    }
  }

  return {
    'Content-Type': 'application/json',
  }
}

async function fetchWithAuth(url: string, options: RequestInit = {}) {
  const headers = await getAuthHeaders()
  const response = await fetch(url, {
    ...options,
    headers: {
      ...headers,
      ...options.headers,
    },
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Request failed' }))
    throw new Error(error.message || error.detail || 'Request failed')
  }

  return response.json()
}

export const dataProvider: DataProvider = {
  getList: async ({ resource, pagination, filters, sorters }) => {
    const { current = 1, pageSize = 10 } = pagination ?? {}

    const params = new URLSearchParams({
      page: String(current),
      page_size: String(pageSize),
    })

    // Add filters
    filters?.forEach(filter => {
      if ('field' in filter && filter.value !== undefined) {
        params.append(filter.field, String(filter.value))
      }
    })

    // Add sorting
    if (sorters && sorters.length > 0) {
      const sorter = sorters[0]
      params.append('sort_by', sorter.field)
      params.append('sort_order', sorter.order)
    }

    const data = await fetchWithAuth(
      `${API_URL}/api/v1/admin/${resource}?${params}`
    )

    return {
      data: data.items || data.data || data,
      total: data.total || data.length || 0,
    }
  },

  getOne: async ({ resource, id }) => {
    const data = await fetchWithAuth(
      `${API_URL}/api/v1/admin/${resource}/${id}`
    )

    return { data }
  },

  create: async ({ resource, variables }) => {
    const data = await fetchWithAuth(
      `${API_URL}/api/v1/admin/${resource}`,
      {
        method: 'POST',
        body: JSON.stringify(variables),
      }
    )

    return { data }
  },

  update: async ({ resource, id, variables }) => {
    const data = await fetchWithAuth(
      `${API_URL}/api/v1/admin/${resource}/${id}`,
      {
        method: 'PATCH',
        body: JSON.stringify(variables),
      }
    )

    return { data }
  },

  deleteOne: async ({ resource, id }) => {
    const data = await fetchWithAuth(
      `${API_URL}/api/v1/admin/${resource}/${id}`,
      {
        method: 'DELETE',
      }
    )

    return { data }
  },

  getApiUrl: () => API_URL,

  custom: async ({ url, method, payload, headers }) => {
    const data = await fetchWithAuth(
      url.startsWith('http') ? url : `${API_URL}${url}`,
      {
        method: method || 'GET',
        body: payload ? JSON.stringify(payload) : undefined,
        headers,
      }
    )

    return { data }
  },
}
