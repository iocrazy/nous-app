import { LayoutDashboard, Users, Video, CreditCard } from 'lucide-react'

export function Dashboard() {
  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Dashboard</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard
          title="Total Users"
          value="--"
          icon={Users}
          color="blue"
        />
        <StatCard
          title="Total Videos"
          value="--"
          icon={Video}
          color="green"
        />
        <StatCard
          title="Active Teams"
          value="--"
          icon={LayoutDashboard}
          color="purple"
        />
        <StatCard
          title="Total Credits"
          value="--"
          icon={CreditCard}
          color="orange"
        />
      </div>

      <div className="mt-8 bg-white rounded-lg shadow p-6">
        <p className="text-gray-500">
          Dashboard statistics will be populated after backend API implementation.
        </p>
      </div>
    </div>
  )
}

interface StatCardProps {
  title: string
  value: string
  icon: React.ComponentType<{ className?: string }>
  color: 'blue' | 'green' | 'purple' | 'orange'
}

function StatCard({ title, value, icon: Icon, color }: StatCardProps) {
  const colorClasses = {
    blue: 'bg-blue-50 text-blue-600',
    green: 'bg-green-50 text-green-600',
    purple: 'bg-purple-50 text-purple-600',
    orange: 'bg-orange-50 text-orange-600',
  }

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-gray-500">{title}</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{value}</p>
        </div>
        <div className={`p-3 rounded-lg ${colorClasses[color]}`}>
          <Icon className="w-6 h-6" />
        </div>
      </div>
    </div>
  )
}
