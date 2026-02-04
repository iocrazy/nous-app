import { useState } from 'react'
import { useList, useUpdate, useDelete } from '@refinedev/core'
import {
  Search,
  Ban,
  Trash2,
  ChevronLeft,
  ChevronRight,
  User,
  CheckCircle,
  XCircle,
  MoreVertical,
} from 'lucide-react'

interface UserData {
  id: string
  email: string | null
  username: string | null
  avatar_url: string | null
  role: string
  is_banned: boolean
  created_at: string
  updated_at: string | null
  last_sign_in_at: string | null
  video_count: number
  team_count: number
}

const ROLES = ['admin', 'user', 'test'] as const
const PAGE_SIZE = 20

export function UserList() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [roleFilter, setRoleFilter] = useState<string>('')
  const [openMenuId, setOpenMenuId] = useState<string | null>(null)

  const { data, isLoading, refetch } = useList<UserData>({
    resource: 'users',
    pagination: { current: page, pageSize: PAGE_SIZE },
    filters: [
      ...(search ? [{ field: 'search', operator: 'contains' as const, value: search }] : []),
      ...(roleFilter ? [{ field: 'role', operator: 'eq' as const, value: roleFilter }] : []),
    ],
  })

  const { mutate: updateUser, isLoading: isUpdating } = useUpdate()
  const { mutate: deleteUser, isLoading: isDeleting } = useDelete()

  const users = data?.data ?? []
  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / PAGE_SIZE)

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    setSearch(searchInput)
    setPage(1)
  }

  const handleRoleChange = (userId: string, newRole: string) => {
    updateUser(
      {
        resource: 'users',
        id: userId,
        values: { role: newRole },
      },
      {
        onSuccess: () => {
          refetch()
        },
      }
    )
  }

  const handleBanToggle = (user: UserData) => {
    updateUser(
      {
        resource: 'users',
        id: user.id,
        values: { is_banned: !user.is_banned },
      },
      {
        onSuccess: () => {
          refetch()
          setOpenMenuId(null)
        },
      }
    )
  }

  const handleDelete = (userId: string) => {
    if (!window.confirm('Are you sure you want to delete this user? This will ban the user.')) {
      return
    }
    deleteUser(
      {
        resource: 'users',
        id: userId,
      },
      {
        onSuccess: () => {
          refetch()
          setOpenMenuId(null)
        },
      }
    )
  }

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '-'
    return new Date(dateStr).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  }

  const getRoleBadgeClass = (role: string) => {
    switch (role) {
      case 'admin':
        return 'bg-purple-100 text-purple-800'
      case 'test':
        return 'bg-yellow-100 text-yellow-800'
      default:
        return 'bg-gray-100 text-gray-800'
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Users</h1>
        <span className="text-sm text-gray-500">
          {total} total users
        </span>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-lg shadow mb-6 p-4">
        <div className="flex flex-col sm:flex-row gap-4">
          {/* Search */}
          <form onSubmit={handleSearch} className="flex-1">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
              <input
                type="text"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Search by email or username..."
                className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              />
            </div>
          </form>

          {/* Role Filter */}
          <div className="w-full sm:w-48">
            <select
              value={roleFilter}
              onChange={(e) => {
                setRoleFilter(e.target.value)
                setPage(1)
              }}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            >
              <option value="">All Roles</option>
              <option value="admin">Admin</option>
              <option value="user">User</option>
              <option value="test">Test</option>
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
                  User
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Role
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Videos
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Teams
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Joined
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {isLoading ? (
                <tr>
                  <td colSpan={7} className="px-6 py-12 text-center">
                    <div className="flex items-center justify-center">
                      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
                      <span className="ml-3 text-gray-500">Loading users...</span>
                    </div>
                  </td>
                </tr>
              ) : users.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-6 py-12 text-center text-gray-500">
                    No users found
                  </td>
                </tr>
              ) : (
                users.map((user) => (
                  <tr key={user.id} className="hover:bg-gray-50">
                    {/* User */}
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center">
                        <div className="flex-shrink-0 h-10 w-10">
                          {user.avatar_url ? (
                            <img
                              className="h-10 w-10 rounded-full object-cover"
                              src={user.avatar_url}
                              alt=""
                            />
                          ) : (
                            <div className="h-10 w-10 rounded-full bg-gray-200 flex items-center justify-center">
                              <User className="h-5 w-5 text-gray-500" />
                            </div>
                          )}
                        </div>
                        <div className="ml-4">
                          <div className="text-sm font-medium text-gray-900">
                            {user.username || 'No username'}
                          </div>
                          <div className="text-sm text-gray-500">
                            {user.email || 'No email'}
                          </div>
                        </div>
                      </div>
                    </td>

                    {/* Role */}
                    <td className="px-6 py-4 whitespace-nowrap">
                      <select
                        value={user.role}
                        onChange={(e) => handleRoleChange(user.id, e.target.value)}
                        disabled={isUpdating}
                        className={`text-xs font-semibold px-2.5 py-1 rounded-full border-0 cursor-pointer ${getRoleBadgeClass(user.role)} focus:ring-2 focus:ring-blue-500`}
                      >
                        {ROLES.map((role) => (
                          <option key={role} value={role}>
                            {role.charAt(0).toUpperCase() + role.slice(1)}
                          </option>
                        ))}
                      </select>
                    </td>

                    {/* Status */}
                    <td className="px-6 py-4 whitespace-nowrap">
                      {user.is_banned ? (
                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-red-100 text-red-800">
                          <XCircle size={14} />
                          Banned
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-green-100 text-green-800">
                          <CheckCircle size={14} />
                          Active
                        </span>
                      )}
                    </td>

                    {/* Videos */}
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {user.video_count}
                    </td>

                    {/* Teams */}
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {user.team_count}
                    </td>

                    {/* Joined */}
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {formatDate(user.created_at)}
                    </td>

                    {/* Actions */}
                    <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                      <div className="relative inline-block text-left">
                        <button
                          onClick={() => setOpenMenuId(openMenuId === user.id ? null : user.id)}
                          className="p-2 rounded-lg hover:bg-gray-100 text-gray-500 hover:text-gray-700"
                        >
                          <MoreVertical size={18} />
                        </button>

                        {openMenuId === user.id && (
                          <>
                            {/* Backdrop to close menu */}
                            <div
                              className="fixed inset-0 z-10"
                              onClick={() => setOpenMenuId(null)}
                            />
                            <div className="absolute right-0 z-20 mt-2 w-48 rounded-lg bg-white shadow-lg ring-1 ring-black ring-opacity-5">
                              <div className="py-1">
                                <button
                                  onClick={() => handleBanToggle(user)}
                                  disabled={isUpdating}
                                  className="flex w-full items-center gap-2 px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 disabled:opacity-50"
                                >
                                  <Ban size={16} />
                                  {user.is_banned ? 'Unban User' : 'Ban User'}
                                </button>
                                <button
                                  onClick={() => handleDelete(user.id)}
                                  disabled={isDeleting}
                                  className="flex w-full items-center gap-2 px-4 py-2 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50"
                                >
                                  <Trash2 size={16} />
                                  Delete User
                                </button>
                              </div>
                            </div>
                          </>
                        )}
                      </div>
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
    </div>
  )
}
