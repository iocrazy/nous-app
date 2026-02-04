import { useGetIdentity, useLogout } from '@refinedev/core'
import { LogOut, User } from 'lucide-react'

interface Identity {
  id: string
  email: string
  name: string
  avatar?: string
  role: string
}

export function Header() {
  const { data: identity } = useGetIdentity<Identity>()
  const { mutate: logout } = useLogout()

  return (
    <header className="h-16 bg-white border-b border-gray-200 flex items-center justify-between px-6">
      <div className="text-lg font-semibold text-gray-700">
        Admin Panel
      </div>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <User size={16} />
          <span>{identity?.name || identity?.email}</span>
          <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs">
            {identity?.role}
          </span>
        </div>
        <button
          onClick={() => logout()}
          className="flex items-center gap-2 px-3 py-1.5 text-sm text-gray-600 hover:text-red-600 transition-colors"
        >
          <LogOut size={16} />
          Logout
        </button>
      </div>
    </header>
  )
}
