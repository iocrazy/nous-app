import type { ReactNode } from 'react'
import { Card, Empty } from '@arco-design/web-react'

export function PlaceholderPage({
  title,
  description,
}: {
  title: string
  description?: ReactNode
}) {
  return (
    <Card title={title}>
      <Empty
        description={
          description ?? 'This page will be implemented in a future phase.'
        }
      />
    </Card>
  )
}
