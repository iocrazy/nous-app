import { Typography, Space, Breadcrumb } from '@arco-design/web-react'
import { IconHome } from '@arco-design/web-react/icon'
import { useNavigate } from 'react-router-dom'
import type { ReactNode } from 'react'

interface PageHeaderProps {
  title: string
  subtitle?: string
  icon?: ReactNode
  breadcrumb?: string[]
  extra?: ReactNode
}

export function PageHeader({ title, subtitle, icon, breadcrumb, extra }: PageHeaderProps) {
  const navigate = useNavigate()

  return (
    <div style={{ marginBottom: 20 }}>
      {breadcrumb && breadcrumb.length > 0 && (
        <Breadcrumb style={{ marginBottom: 8 }}>
          <Breadcrumb.Item onClick={() => navigate('/')}>
            <IconHome />
          </Breadcrumb.Item>
          {breadcrumb.map((item, i) => (
            <Breadcrumb.Item key={i}>{item}</Breadcrumb.Item>
          ))}
        </Breadcrumb>
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <Space size={12} align="start">
          {icon && (
            <div
              style={{
                width: 40,
                height: 40,
                borderRadius: 10,
                background: 'var(--color-primary-light-1)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 20,
                color: 'rgb(var(--primary-6))',
                flexShrink: 0,
              }}
            >
              {icon}
            </div>
          )}
          <div>
            <Typography.Title heading={5} style={{ margin: 0 }}>
              {title}
            </Typography.Title>
            {subtitle && (
              <Typography.Text type="secondary" style={{ fontSize: 13, marginTop: 2 }}>
                {subtitle}
              </Typography.Text>
            )}
          </div>
        </Space>
        {extra && <div>{extra}</div>}
      </div>
    </div>
  )
}
