import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  Users,
  Users2,
  Video,
  Tags,
  CreditCard,
  ScrollText,
  Key,
  Settings,
} from 'lucide-react'

const menuItems = [
  { path: '/', label: 'Dashboard', icon: LayoutDashboard },
  { path: '/users', label: 'Users', icon: Users },
  { path: '/teams', label: 'Teams', icon: Users2 },
  { path: '/videos', label: 'Videos', icon: Video },
  { path: '/tags', label: 'Tags', icon: Tags },
  { path: '/credits', label: 'Credits', icon: CreditCard },
  { path: '/audit-logs', label: 'Audit Logs', icon: ScrollText },
  { path: '/api-keys', label: 'API Keys', icon: Key },
  { path: '/settings', label: 'Settings', icon: Settings },
]

export function Sidebar() {
  return (
    <aside className="w-64 bg-gray-900 text-white min-h-screen">
      <div className="p-4 border-b border-gray-800">
        <h1 className="text-xl font-bold">MediaHub Admin</h1>
      </div>
      <nav className="p-4">
        <ul className="space-y-2">
          {menuItems.map((item) => (
            <li key={item.path}>
              <NavLink
                to={item.path}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-4 py-2 rounded-lg transition-colors ${
                    isActive
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-300 hover:bg-gray-800'
                  }`
                }
              >
                <item.icon size={20} />
                <span>{item.label}</span>
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  )
}
