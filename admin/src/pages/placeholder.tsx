import { Card, Empty } from '@arco-design/web-react'

export function PlaceholderPage({ title }: { title: string }) {
  return (
    <Card title={title}>
      <Empty description="This page will be implemented in a future phase." />
    </Card>
  )
}
