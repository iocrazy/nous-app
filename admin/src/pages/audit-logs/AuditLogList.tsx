import { useState } from 'react'
import { useList } from '@refinedev/core'
import {
  ChevronLeft,
  ChevronRight,
  FileText,
  Eye,
  X,
} from 'lucide-react'

interface AuditLog {
  id: string
  admin_id: string
  admin_email: string | null
  action: string
  target_type: string
  target_id: string
  details: Record<string, unknown> | null
  ip_address: string | null
  created_at: string
}

const ACTION_TYPES = [
  { value: '', label: 'All Actions' },
  { value: 'create', label: 'Create' },
  { value: 'update', label: 'Update' },
  { value: 'delete', label: 'Delete' },
  { value: 'ban', label: 'Ban' },
  { value: 'unban', label: 'Unban' },
  { value: 'role_change', label: 'Role Change' },
]

const TARGET_TYPES = [
  { value: '', label: 'All Targets' },
  { value: 'user', label: 'User' },
  { value: 'team', label: 'Team' },
  { value: 'video', label: 'Video' },
  { value: 'tag', label: 'Tag' },
  { value: 'api_key', label: 'API Key' },
]

const PAGE_SIZE = 50

export function AuditLogList() {
  const [page, setPage] = useState(1)
  const [actionFilter, setActionFilter] = useState('')
  const [targetFilter, setTargetFilter] = useState('')
  const [detailsModal, setDetailsModal] = useState<AuditLog | null>(null)

  const { data, isLoading } = useList<AuditLog>({
    resource: 'audit-logs',
    pagination: { current: page, pageSize: PAGE_SIZE },
    filters: [
      ...(actionFilter ? [{ field: 'action', operator: 'eq' as const, value: actionFilter }] : []),
      ...(targetFilter ? [{ field: 'target_type', operator: 'eq' as const, value: targetFilter }] : []),
    ],
    sorters: [{ field: 'created_at', order: 'desc' }],
  })

  const logs = data?.data ?? []
  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / PAGE_SIZE)

  const getActionColor = (action: string): string => {
    const actionLower = action.toLowerCase()
    if (actionLower.includes('delete')) return 'bg-red-100 text-red-700'
    if (actionLower.includes('ban')) return 'bg-orange-100 text-orange-700'
    if (actionLower.includes('create')) return 'bg-green-100 text-green-700'
    if (actionLower.includes('update') || actionLower.includes('change')) return 'bg-blue-100 text-blue-700'
    if (actionLower.includes('unban')) return 'bg-emerald-100 text-emerald-700'
    return 'bg-gray-100 text-gray-700'
  }

  const formatDateTime = (dateStr: string | null): string => {
    if (!dateStr) return '-'
    return new Date(dateStr).toLocaleString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  }

  const formatAction = (action: string): string => {
    return action
      .split('_')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ')
  }

  const formatTargetType = (type: string): string => {
    return type
      .split('_')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ')
  }

  const truncateId = (id: string): string => {
    if (id.length <= 12) return id
    return `${id.slice(0, 8)}...`
  }

  const renderDetailsPreview = (details: Record<string, unknown> | null): string => {
    if (!details) return '-'
    const str = JSON.stringify(details)
    if (str.length <= 50) return str
    return str.slice(0, 47) + '...'
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Audit Logs</h1>
        <span className="text-sm text-gray-500">
          {total} total entries
        </span>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-lg shadow mb-6 p-4">
        <div className="flex flex-col sm:flex-row gap-4">
          {/* Action Filter */}
          <div className="w-full sm:w-48">
            <select
              value={actionFilter}
              onChange={(e) => {
                setActionFilter(e.target.value)
                setPage(1)
              }}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            >
              {ACTION_TYPES.map((action) => (
                <option key={action.value} value={action.value}>
                  {action.label}
                </option>
              ))}
            </select>
          </div>

          {/* Target Filter */}
          <div className="w-full sm:w-48">
            <select
              value={targetFilter}
              onChange={(e) => {
                setTargetFilter(e.target.value)
                setPage(1)
              }}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            >
              {TARGET_TYPES.map((target) => (
                <option key={target.value} value={target.value}>
                  {target.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="bg-white rounded-lg shadow overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Time
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Admin
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Action
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Target
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Details
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  IP Address
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {isLoading ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center">
                    <div className="flex items-center justify-center">
                      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
                      <span className="ml-3 text-gray-500">Loading audit logs...</span>
                    </div>
                  </td>
                </tr>
              ) : logs.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center">
                    <div className="flex flex-col items-center justify-center text-gray-500">
                      <FileText size={48} className="mb-3 text-gray-300" />
                      <p>No audit logs found</p>
                      {(actionFilter || targetFilter) && (
                        <p className="text-sm mt-1">Try adjusting your filters</p>
                      )}
                    </div>
                  </td>
                </tr>
              ) : (
                logs.map((log) => (
                  <tr key={log.id} className="hover:bg-gray-50">
                    {/* Time */}
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {formatDateTime(log.created_at)}
                    </td>

                    {/* Admin */}
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="text-sm text-gray-900">
                        {log.admin_email || 'Unknown'}
                      </div>
                      <div className="text-xs text-gray-400">
                        {truncateId(log.admin_id)}
                      </div>
                    </td>

                    {/* Action */}
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className={`inline-flex px-2.5 py-1 text-xs font-semibold rounded-full ${getActionColor(log.action)}`}>
                        {formatAction(log.action)}
                      </span>
                    </td>

                    {/* Target */}
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="text-sm text-gray-900">
                        {formatTargetType(log.target_type)}
                      </div>
                      <div className="text-xs text-gray-400 font-mono">
                        {truncateId(log.target_id)}
                      </div>
                    </td>

                    {/* Details */}
                    <td className="px-6 py-4">
                      {log.details ? (
                        <div className="flex items-center gap-2">
                          <span className="text-sm text-gray-500 font-mono max-w-xs truncate">
                            {renderDetailsPreview(log.details)}
                          </span>
                          <button
                            onClick={() => setDetailsModal(log)}
                            className="p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600"
                            title="View full details"
                          >
                            <Eye size={16} />
                          </button>
                        </div>
                      ) : (
                        <span className="text-sm text-gray-400">-</span>
                      )}
                    </td>

                    {/* IP Address */}
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 font-mono">
                      {log.ip_address || '-'}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="bg-white px-4 py-3 flex items-center justify-between border-t border-gray-200 sm:px-6">
            <div className="flex-1 flex justify-between sm:hidden">
              <button
                onClick={() => setPage(Math.max(1, page - 1))}
                disabled={page === 1}
                className="relative inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Previous
              </button>
              <button
                onClick={() => setPage(Math.min(totalPages, page + 1))}
                disabled={page === totalPages}
                className="ml-3 relative inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Next
              </button>
            </div>
            <div className="hidden sm:flex-1 sm:flex sm:items-center sm:justify-between">
              <div>
                <p className="text-sm text-gray-700">
                  Showing <span className="font-medium">{(page - 1) * PAGE_SIZE + 1}</span> to{' '}
                  <span className="font-medium">{Math.min(page * PAGE_SIZE, total)}</span> of{' '}
                  <span className="font-medium">{total}</span> results
                </p>
              </div>
              <div>
                <nav className="relative z-0 inline-flex rounded-md shadow-sm -space-x-px" aria-label="Pagination">
                  <button
                    onClick={() => setPage(Math.max(1, page - 1))}
                    disabled={page === 1}
                    className="relative inline-flex items-center px-2 py-2 rounded-l-md border border-gray-300 bg-white text-sm font-medium text-gray-500 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <ChevronLeft size={18} />
                  </button>

                  {/* Page numbers */}
                  {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                    let pageNum: number
                    if (totalPages <= 5) {
                      pageNum = i + 1
                    } else if (page <= 3) {
                      pageNum = i + 1
                    } else if (page >= totalPages - 2) {
                      pageNum = totalPages - 4 + i
                    } else {
                      pageNum = page - 2 + i
                    }
                    return (
                      <button
                        key={pageNum}
                        onClick={() => setPage(pageNum)}
                        className={`relative inline-flex items-center px-4 py-2 border text-sm font-medium ${
                          page === pageNum
                            ? 'z-10 bg-blue-50 border-blue-500 text-blue-600'
                            : 'bg-white border-gray-300 text-gray-500 hover:bg-gray-50'
                        }`}
                      >
                        {pageNum}
                      </button>
                    )
                  })}

                  <button
                    onClick={() => setPage(Math.min(totalPages, page + 1))}
                    disabled={page === totalPages}
                    className="relative inline-flex items-center px-2 py-2 rounded-r-md border border-gray-300 bg-white text-sm font-medium text-gray-500 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <ChevronRight size={18} />
                  </button>
                </nav>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Details Modal */}
      {detailsModal && (
        <div className="fixed inset-0 z-50 overflow-y-auto">
          <div className="flex items-center justify-center min-h-screen px-4 pt-4 pb-20 text-center sm:block sm:p-0">
            {/* Backdrop */}
            <div
              className="fixed inset-0 transition-opacity bg-gray-500 bg-opacity-75"
              onClick={() => setDetailsModal(null)}
            />

            {/* Modal */}
            <div className="inline-block w-full max-w-2xl my-8 overflow-hidden text-left align-middle transition-all transform bg-white rounded-lg shadow-xl">
              {/* Header */}
              <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
                <h3 className="text-lg font-semibold text-gray-900">
                  Audit Log Details
                </h3>
                <button
                  onClick={() => setDetailsModal(null)}
                  className="p-2 rounded-lg hover:bg-gray-100 text-gray-400 hover:text-gray-600"
                >
                  <X size={20} />
                </button>
              </div>

              {/* Content */}
              <div className="px-6 py-4">
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs font-medium text-gray-500 uppercase">Time</label>
                      <p className="mt-1 text-sm text-gray-900">{formatDateTime(detailsModal.created_at)}</p>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-500 uppercase">Admin</label>
                      <p className="mt-1 text-sm text-gray-900">{detailsModal.admin_email || 'Unknown'}</p>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-500 uppercase">Action</label>
                      <span className={`inline-flex mt-1 px-2.5 py-1 text-xs font-semibold rounded-full ${getActionColor(detailsModal.action)}`}>
                        {formatAction(detailsModal.action)}
                      </span>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-500 uppercase">Target</label>
                      <p className="mt-1 text-sm text-gray-900">
                        {formatTargetType(detailsModal.target_type)} - <span className="font-mono">{detailsModal.target_id}</span>
                      </p>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-500 uppercase">IP Address</label>
                      <p className="mt-1 text-sm text-gray-900 font-mono">{detailsModal.ip_address || '-'}</p>
                    </div>
                  </div>

                  <div>
                    <label className="block text-xs font-medium text-gray-500 uppercase mb-2">Details (JSON)</label>
                    <pre className="p-4 bg-gray-50 rounded-lg overflow-x-auto text-sm text-gray-800 font-mono">
                      {detailsModal.details ? JSON.stringify(detailsModal.details, null, 2) : 'No details'}
                    </pre>
                  </div>
                </div>
              </div>

              {/* Footer */}
              <div className="flex justify-end px-6 py-4 border-t border-gray-200 bg-gray-50">
                <button
                  onClick={() => setDetailsModal(null)}
                  className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
