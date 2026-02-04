import { useState } from 'react'
import { useList, useDelete, useCustom } from '@refinedev/core'
import {
  Search,
  Trash2,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  Users,
  Video,
  Copy,
  Check,
} from 'lucide-react'

interface Team {
  id: string
  name: string
  owner_id: string
  owner_email: string | null
  invite_code: string
  member_count: number
  video_count: number
  created_at: string
}

interface TeamMember {
  user_id: string
  email: string
  username: string | null
  role: string
  joined_at: string
}

const PAGE_SIZE = 20

export function TeamList() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [expandedTeam, setExpandedTeam] = useState<string | null>(null)
  const [copiedCode, setCopiedCode] = useState<string | null>(null)

  const { data, isLoading, refetch } = useList<Team>({
    resource: 'teams',
    pagination: { current: page, pageSize: PAGE_SIZE },
    filters: [
      ...(search ? [{ field: 'search', operator: 'contains' as const, value: search }] : []),
    ],
  })

  const { mutate: deleteTeam, isLoading: isDeleting } = useDelete()

  // Fetch team members when a team is expanded
  const { data: membersData, isLoading: isMembersLoading } = useCustom<{ items: TeamMember[] }>({
    url: `/api/v1/admin/teams/${expandedTeam}/members`,
    method: 'get',
    queryOptions: {
      enabled: !!expandedTeam,
    },
  })

  const teams = data?.data ?? []
  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / PAGE_SIZE)
  const members = membersData?.data?.items ?? []

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    setSearch(searchInput)
    setPage(1)
  }

  const handleDelete = (teamId: string, teamName: string) => {
    if (!window.confirm(`Are you sure you want to delete team "${teamName}"? This action cannot be undone.`)) {
      return
    }
    deleteTeam(
      {
        resource: 'teams',
        id: teamId,
      },
      {
        onSuccess: () => {
          refetch()
          if (expandedTeam === teamId) {
            setExpandedTeam(null)
          }
        },
      }
    )
  }

  const toggleExpand = (teamId: string) => {
    setExpandedTeam(expandedTeam === teamId ? null : teamId)
  }

  const copyInviteCode = async (code: string) => {
    await navigator.clipboard.writeText(code)
    setCopiedCode(code)
    setTimeout(() => setCopiedCode(null), 2000)
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
      case 'owner':
        return 'bg-purple-100 text-purple-800'
      case 'admin':
        return 'bg-blue-100 text-blue-800'
      default:
        return 'bg-gray-100 text-gray-800'
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Teams</h1>
        <span className="text-sm text-gray-500">
          {total} total teams
        </span>
      </div>

      {/* Search */}
      <div className="bg-white rounded-lg shadow mb-6 p-4">
        <form onSubmit={handleSearch}>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search by team name..."
              className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            />
          </div>
        </form>
      </div>

      {/* Team Cards */}
      <div className="space-y-4">
        {isLoading ? (
          <div className="bg-white rounded-lg shadow p-12">
            <div className="flex items-center justify-center">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
              <span className="ml-3 text-gray-500">Loading teams...</span>
            </div>
          </div>
        ) : teams.length === 0 ? (
          <div className="bg-white rounded-lg shadow p-12 text-center text-gray-500">
            No teams found
          </div>
        ) : (
          teams.map((team) => (
            <div key={team.id} className="bg-white rounded-lg shadow overflow-hidden">
              {/* Team Card Header */}
              <div className="p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-4 flex-1 min-w-0">
                    {/* Team Icon */}
                    <div className="flex-shrink-0 h-12 w-12 rounded-lg bg-blue-100 flex items-center justify-center">
                      <Users className="h-6 w-6 text-blue-600" />
                    </div>

                    {/* Team Info */}
                    <div className="flex-1 min-w-0">
                      <h3 className="text-lg font-semibold text-gray-900 truncate">
                        {team.name}
                      </h3>
                      <p className="text-sm text-gray-500 truncate">
                        Owner: {team.owner_email || 'Unknown'}
                      </p>
                    </div>
                  </div>

                  {/* Stats & Actions */}
                  <div className="flex items-center gap-6">
                    {/* Stats */}
                    <div className="hidden sm:flex items-center gap-6 text-sm text-gray-500">
                      <div className="flex items-center gap-1.5">
                        <Users size={16} className="text-gray-400" />
                        <span>{team.member_count} members</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <Video size={16} className="text-gray-400" />
                        <span>{team.video_count} videos</span>
                      </div>
                    </div>

                    {/* Invite Code */}
                    <div className="hidden md:flex items-center gap-2">
                      <code className="px-2 py-1 bg-gray-100 rounded text-sm font-mono text-gray-700">
                        {team.invite_code}
                      </code>
                      <button
                        onClick={() => copyInviteCode(team.invite_code)}
                        className="p-1.5 rounded hover:bg-gray-100 text-gray-500 hover:text-gray-700"
                        title="Copy invite code"
                      >
                        {copiedCode === team.invite_code ? (
                          <Check size={16} className="text-green-600" />
                        ) : (
                          <Copy size={16} />
                        )}
                      </button>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => toggleExpand(team.id)}
                        className="p-2 rounded-lg hover:bg-gray-100 text-gray-500 hover:text-gray-700"
                        title={expandedTeam === team.id ? 'Collapse' : 'Show members'}
                      >
                        {expandedTeam === team.id ? (
                          <ChevronUp size={20} />
                        ) : (
                          <ChevronDown size={20} />
                        )}
                      </button>
                      <button
                        onClick={() => handleDelete(team.id, team.name)}
                        disabled={isDeleting}
                        className="p-2 rounded-lg hover:bg-red-50 text-gray-500 hover:text-red-600 disabled:opacity-50"
                        title="Delete team"
                      >
                        <Trash2 size={20} />
                      </button>
                    </div>
                  </div>
                </div>

                {/* Mobile Stats */}
                <div className="flex sm:hidden items-center gap-4 mt-3 text-sm text-gray-500">
                  <div className="flex items-center gap-1.5">
                    <Users size={14} className="text-gray-400" />
                    <span>{team.member_count}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Video size={14} className="text-gray-400" />
                    <span>{team.video_count}</span>
                  </div>
                  <div className="flex items-center gap-1.5 ml-auto">
                    <code className="px-2 py-0.5 bg-gray-100 rounded text-xs font-mono">
                      {team.invite_code}
                    </code>
                    <button
                      onClick={() => copyInviteCode(team.invite_code)}
                      className="p-1 rounded hover:bg-gray-100"
                    >
                      {copiedCode === team.invite_code ? (
                        <Check size={14} className="text-green-600" />
                      ) : (
                        <Copy size={14} className="text-gray-400" />
                      )}
                    </button>
                  </div>
                </div>

                {/* Created Date */}
                <div className="mt-2 text-xs text-gray-400">
                  Created {formatDate(team.created_at)}
                </div>
              </div>

              {/* Expandable Members Section */}
              {expandedTeam === team.id && (
                <div className="border-t border-gray-200 bg-gray-50">
                  <div className="p-4">
                    <h4 className="text-sm font-semibold text-gray-700 mb-3">Team Members</h4>
                    {isMembersLoading ? (
                      <div className="flex items-center justify-center py-4">
                        <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-blue-600"></div>
                        <span className="ml-2 text-sm text-gray-500">Loading members...</span>
                      </div>
                    ) : members.length === 0 ? (
                      <p className="text-sm text-gray-500 py-2">No members found</p>
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="min-w-full divide-y divide-gray-200">
                          <thead>
                            <tr>
                              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                User
                              </th>
                              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                Role
                              </th>
                              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                Joined
                              </th>
                            </tr>
                          </thead>
                          <tbody className="bg-white divide-y divide-gray-200">
                            {members.map((member) => (
                              <tr key={member.user_id}>
                                <td className="px-4 py-3 whitespace-nowrap">
                                  <div>
                                    <div className="text-sm font-medium text-gray-900">
                                      {member.username || 'No username'}
                                    </div>
                                    <div className="text-sm text-gray-500">
                                      {member.email}
                                    </div>
                                  </div>
                                </td>
                                <td className="px-4 py-3 whitespace-nowrap">
                                  <span className={`inline-flex px-2 py-0.5 text-xs font-semibold rounded-full ${getRoleBadgeClass(member.role)}`}>
                                    {member.role.charAt(0).toUpperCase() + member.role.slice(1)}
                                  </span>
                                </td>
                                <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-500">
                                  {formatDate(member.joined_at)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="bg-white rounded-lg shadow mt-6 px-4 py-3 flex items-center justify-between sm:px-6">
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
  )
}
