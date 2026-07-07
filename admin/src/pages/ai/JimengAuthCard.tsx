import { useState, useEffect, useCallback } from 'react'
import { Card, Button, Badge, Space, Typography, Modal, Message, Popconfirm, Spin } from '@arco-design/web-react'
import { IconCopy, IconLaunch, IconSync } from '@arco-design/web-react/icon'
import {
  useJimengStatus,
  useJimengLogin,
  useJimengLogout,
  type JimengLoginMaterial,
} from '../../api/endpoints/jimeng'

const { Text, Title } = Typography

// While the login modal is open we poll status fast so the badge flips to green
// the moment the user authorizes; otherwise a slow background refresh is enough.
const POLL_OPEN_MS = 5000
const POLL_IDLE_MS = 30000

/**
 * Jimeng (即梦) CLI login panel — authorize / renew the dreamina session from
 * the admin UI instead of an SSH `dreamina login` on the NAS. Shows the login
 * badge + credit, and a Login button that opens the OAuth device-flow link.
 */
export function JimengAuthCard() {
  const [modalOpen, setModalOpen] = useState(false)
  const [material, setMaterial] = useState<JimengLoginMaterial | null>(null)

  const status = useJimengStatus(modalOpen ? POLL_OPEN_MS : POLL_IDLE_MS)
  const login = useJimengLogin()
  const logout = useJimengLogout()

  const loggedIn = !!status.data?.logged_in

  // The device flow completed: the background poll sees logged_in → close the
  // modal and confirm.
  useEffect(() => {
    if (modalOpen && loggedIn) {
      setModalOpen(false)
      setMaterial(null)
      Message.success('Jimeng CLI is now logged in')
    }
  }, [modalOpen, loggedIn])

  const handleLogin = useCallback(async () => {
    try {
      const result = await login.mutateAsync()
      if (result.status === 'already') {
        Message.success('Already logged in')
        status.refetch()
        return
      }
      setMaterial(result)
      setModalOpen(true)
    } catch {
      Message.error('Failed to start login')
    }
  }, [login, status])

  const handleLogout = useCallback(async () => {
    try {
      await logout.mutateAsync()
      Message.success('Logged out')
    } catch {
      Message.error('Failed to log out')
    }
  }, [logout])

  const copy = useCallback((value: string) => {
    navigator.clipboard?.writeText(value).then(
      () => Message.success('Copied'),
      () => Message.error('Copy failed'),
    )
  }, [])

  return (
    <Card style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 15 }}>Jimeng CLI (即梦)</div>
          <div style={{ marginTop: 6 }}>
            {status.isLoading ? (
              <Spin size={14} />
            ) : loggedIn ? (
              <Space>
                <Badge status="success" text="Logged in" />
                {status.data?.credit != null && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    Credit: {status.data.credit}
                  </Text>
                )}
              </Space>
            ) : (
              <Badge
                status="error"
                text={status.data?.error ? `Not logged in (${status.data.error})` : 'Not logged in'}
              />
            )}
          </div>
        </div>
        <Space>
          <Button icon={<IconSync />} onClick={() => status.refetch()} size="small">
            Refresh
          </Button>
          <Button type="primary" loading={login.isPending} onClick={handleLogin}>
            {loggedIn ? 'Re-login' : 'Login'}
          </Button>
          {loggedIn && (
            <Popconfirm title="Log out of Jimeng CLI?" onOk={handleLogout}>
              <Button status="danger" loading={logout.isPending}>
                Logout
              </Button>
            </Popconfirm>
          )}
        </Space>
      </div>

      <Modal
        title="Authorize Jimeng CLI"
        visible={modalOpen}
        onCancel={() => {
          setModalOpen(false)
          setMaterial(null)
        }}
        footer={null}
        autoFocus={false}
      >
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Text>
            Open the authorization link and approve with the 即梦 subscription
            account. This dialog closes automatically once login completes.
          </Text>
          {material?.verification_uri && (
            <div>
              <Title heading={6} style={{ marginBottom: 6 }}>
                Authorization link
              </Title>
              <Space>
                <Button
                  type="primary"
                  icon={<IconLaunch />}
                  onClick={() => window.open(material.verification_uri, '_blank', 'noopener')}
                >
                  Open link
                </Button>
                <Button icon={<IconCopy />} onClick={() => copy(material.verification_uri!)}>
                  Copy
                </Button>
              </Space>
              <div
                style={{
                  fontSize: 12,
                  color: 'var(--color-text-3)',
                  fontFamily: 'monospace',
                  wordBreak: 'break-all',
                  marginTop: 8,
                }}
              >
                {material.verification_uri}
              </div>
            </div>
          )}
          {material?.user_code && (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                User code
              </Text>
              <div style={{ fontFamily: 'monospace', fontSize: 13 }}>{material.user_code}</div>
            </div>
          )}
          <Space>
            <Spin size={14} />
            <Text type="secondary" style={{ fontSize: 12 }}>
              Waiting for authorization…
            </Text>
          </Space>
        </Space>
      </Modal>
    </Card>
  )
}
