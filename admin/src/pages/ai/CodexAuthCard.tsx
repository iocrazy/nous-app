import { Card, Button, Badge, Space, Typography } from '@arco-design/web-react'
import { IconSync } from '@arco-design/web-react/icon'

import { useCodexStatus } from '../../api/endpoints/codex'

const { Text, Title } = Typography

const POLL_IDLE_MS = 30000

/** Human hint per stable provider error code (codex_cli.CodexCliError). */
const ERROR_HINTS: Record<string, string> = {
  not_logged_in: 'Session expired or missing — run `codex login` on the host, see docs/runbook/codex-image.md',
  cli_missing: 'gpt-image-2-skill binary missing from the image — rebuild the backend image',
  timeout: 'Probe timed out — check the container',
  health_failed: 'Doctor probe failed — check worker logs for [codex-cli]',
}

/**
 * Codex (GPT Image 2) CLI status panel — the session lives on the HOST
 * (`codex login`, browser OAuth) and is bind-mounted in, so unlike the
 * jimeng card there is no in-panel login; this card only makes the state
 * visible (green/red badge + the stable error code) without a shell.
 */
export function CodexAuthCard() {
  const status = useCodexStatus(POLL_IDLE_MS)
  const loggedIn = !!status.data?.logged_in
  const error = status.data?.error

  return (
    <Card style={{ marginTop: 16 }}>
      <Space direction="vertical" size="small" style={{ width: '100%' }}>
        <Space align="center">
          <Title heading={6} style={{ margin: 0 }}>
            Codex CLI (GPT Image 2)
          </Title>
          {status.isLoading ? (
            <Badge status="processing" text="Checking..." />
          ) : loggedIn ? (
            <Badge status="success" text="Logged in" />
          ) : (
            <Badge status="error" text={error ?? 'Not logged in'} />
          )}
          <Button
            size="mini"
            icon={<IconSync />}
            loading={status.isFetching}
            onClick={() => status.refetch()}
          >
            Refresh
          </Button>
        </Space>
        {!loggedIn && error && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {ERROR_HINTS[error] ?? error}
          </Text>
        )}
        <Text type="secondary" style={{ fontSize: 12 }}>
          Session is created on the host (`codex login`) and bind-mounted into
          the workers — no in-panel login. Runbook: docs/runbook/codex-image.md
        </Text>
      </Space>
    </Card>
  )
}
