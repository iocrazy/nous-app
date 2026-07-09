import type { ReactNode } from 'react'

/**
 * Shared section header for the System settings pages (AI Governance, Memory,
 * Settings). Icon chip + bold title + gray subtitle — mirrors the user app's
 * section-header pattern so a section TITLE clearly outranks its content rows.
 */
export function SectionHeader({
  icon,
  title,
  subtitle,
}: {
  icon: ReactNode
  title: ReactNode
  subtitle?: string
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 12,
        marginTop: 24,
        marginBottom: 4,
      }}
    >
      <div
        style={{
          flexShrink: 0,
          width: 30,
          height: 30,
          borderRadius: 8,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'rgb(var(--primary-1))',
          color: 'rgb(var(--primary-6))',
          fontSize: 16,
        }}
      >
        {icon}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 700, fontSize: 15, lineHeight: '20px' }}>{title}</div>
        {subtitle && (
          <div style={{ color: 'var(--color-text-3)', fontSize: 12, marginTop: 2 }}>{subtitle}</div>
        )}
      </div>
    </div>
  )
}
