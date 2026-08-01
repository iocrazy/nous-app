import { useState } from 'react'
import { Button, Tag, Typography } from '@arco-design/web-react'
import { useMediaStorageDetail, useVerifyMedia } from '../../api/endpoints/storage'
import { formatBytes } from '../../utils/format'

const KIND_LABEL: Record<string, string> = {
  video: 'Video',
  cover: 'Cover',
  thumbnail: 'Thumbnail',
  sprite: 'Preview Sprite',
  hls: 'HLS',
}

export function StorageSection({ mediaId }: { mediaId: number }) {
  const { data } = useMediaStorageDetail(mediaId)
  const verify = useVerifyMedia()
  const [results, setResults] = useState<Record<string, boolean | null>>({})

  if (!data) return null

  const dot = (kind: string, presentInDb: boolean) => {
    const v = results[kind]
    if (v === true) return <Tag color="green" size="small">OK</Tag>
    if (v === false) return <Tag color="red" size="small">Missing</Tag>
    if (v === null && kind in results)
      return <Tag color="gray" size="small">Unknown</Tag>
    return presentInDb
      ? <Tag color="arcoblue" size="small">In DB</Tag>
      : <Tag color="gray" size="small">—</Tag>
  }

  return (
    <div style={{ marginTop: 16 }}>
      <Typography.Title heading={6} style={{ marginBottom: 8 }}>
        Storage
      </Typography.Title>
      {data.assets.map((a) => (
        <div
          key={a.kind}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '6px 0',
            borderBottom: '1px dashed var(--color-border-2)',
          }}
        >
          <span style={{ width: 110, fontWeight: 600, fontSize: 12 }}>
            {KIND_LABEL[a.kind] ?? a.kind}
          </span>
          <Typography.Text
            ellipsis={{ showTooltip: true }}
            style={{
              flex: 1,
              fontFamily: 'monospace',
              fontSize: 11,
              color: 'var(--color-text-3)',
            }}
          >
            {a.key ?? '—'}
          </Typography.Text>
          {a.size_bytes != null && (
            <span style={{ fontFamily: 'monospace', fontSize: 11 }}>
              {formatBytes(a.size_bytes)}
            </span>
          )}
          {dot(a.kind, a.present_in_db)}
        </div>
      ))}
      <Button
        size="small"
        style={{ marginTop: 10 }}
        loading={verify.isPending}
        onClick={() =>
          verify.mutate(mediaId, {
            onSuccess: (rs) => {
              const next: Record<string, boolean | null> = {}
              rs.forEach((r) => {
                next[r.kind] = r.exists
              })
              setResults(next)
            },
          })
        }
      >
        Verify on S3
      </Button>
    </div>
  )
}
