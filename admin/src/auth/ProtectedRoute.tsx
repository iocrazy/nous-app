import { Navigate, Outlet } from 'react-router-dom'
import { Spin } from '@arco-design/web-react'
import { useAuth } from './AuthProvider'

export function ProtectedRoute() {
  const { user, isAdmin, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spin size={40} />
      </div>
    )
  }

  if (!user || !isAdmin) {
    return <Navigate to="/login" replace />
  }

  return <Outlet />
}
