import { Typography } from '@arco-design/web-react'
import { IconEmpty } from '@arco-design/web-react/icon'
import type { ReactNode } from 'react'

interface EmptyStateProps {
  icon?: ReactNode
  description?: string
}

export function EmptyState({ icon, description = 'No data found' }: EmptyStateProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '40px 0',
        gap: 8,
      }}
    >
      <div style={{ fontSize: 32, color: 'var(--color-text-4)' }}>
        {icon || <IconEmpty />}
      </div>
      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
        {description}
      </Typography.Text>
    </div>
  )
}
