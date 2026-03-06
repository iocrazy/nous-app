import { useState, useEffect } from 'react'
import {
  Card,
  Typography,
  Switch,
  Select,
  Button,
  Message,
  Space,
  Spin,
  Checkbox,
} from '@arco-design/web-react'
import { IconSave } from '@arco-design/web-react/icon'
import {
  useTranscodeSettings,
  useUpdateTranscodeSettings,
} from '../../api/endpoints/transcode'

const TIER_INFO: Record<string, { resolution: string; bitrate: string }> = {
  '480p': { resolution: '854 x 480', bitrate: '1500 kbps' },
  '720p': { resolution: '1280 x 720', bitrate: '4000 kbps' },
  '1080p': { resolution: '1920 x 1080', bitrate: '8000 kbps' },
}

const ENCODER_OPTIONS = [
  { value: 'auto', label: 'Auto Detect (GPU preferred)' },
  { value: 'libx264', label: 'libx264 (CPU)' },
  { value: 'h264_nvenc', label: 'h264_nvenc (NVIDIA GPU)' },
  { value: 'h264_videotoolbox', label: 'VideoToolbox (macOS)' },
  { value: 'h264_qsv', label: 'h264_qsv (Intel QSV)' },
]

const PRESET_OPTIONS = [
  { value: 'ultrafast', label: 'Ultrafast (lowest quality)' },
  { value: 'veryfast', label: 'Very Fast' },
  { value: 'fast', label: 'Fast' },
  { value: 'medium', label: 'Medium (balanced)' },
  { value: 'slow', label: 'Slow (better quality)' },
  { value: 'veryslow', label: 'Very Slow (best quality)' },
]

function SettingRow({
  label,
  description,
  children,
}: {
  label: string
  description?: string
  children: React.ReactNode
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '16px 20px',
        borderRadius: 8,
        backgroundColor: 'var(--color-fill-1)',
        marginBottom: 12,
      }}
    >
      <div style={{ flex: 1 }}>
        <Typography.Text style={{ fontWeight: 600, fontSize: 14 }}>{label}</Typography.Text>
        {description && (
          <div style={{ marginTop: 4 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {description}
            </Typography.Text>
          </div>
        )}
      </div>
      <div style={{ flexShrink: 0, marginLeft: 24 }}>{children}</div>
    </div>
  )
}

export function TranscodeConfig() {
  const { data: settings, isLoading } = useTranscodeSettings()
  const updateSettings = useUpdateTranscodeSettings()

  const [enabled, setEnabled] = useState(true)
  const [tiers, setTiers] = useState<string[]>(['480p', '720p', '1080p'])
  const [encoder, setEncoder] = useState('auto')
  const [preset, setPreset] = useState('medium')
  const [parallelTiers, setParallelTiers] = useState(true)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (settings) {
      setEnabled(settings.transcode_enabled)
      setTiers(settings.transcode_tiers.split(',').map((t) => t.trim()).filter(Boolean))
      setEncoder(settings.ffmpeg_encoder)
      setPreset(settings.ffmpeg_preset)
      setParallelTiers(settings.transcode_parallel_tiers)
      setDirty(false)
    }
  }, [settings])

  const handleSave = () => {
    if (tiers.length === 0) {
      Message.error('Select at least one resolution tier')
      return
    }
    updateSettings.mutate(
      {
        transcode_enabled: enabled,
        transcode_tiers: tiers.join(','),
        ffmpeg_encoder: encoder,
        ffmpeg_preset: preset,
        transcode_parallel_tiers: parallelTiers,
      },
      {
        onSuccess: () => {
          Message.success('Settings saved')
          setDirty(false)
        },
        onError: (err) => Message.error(err.message || 'Failed to save settings'),
      },
    )
  }

  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 80 }}>
        <Spin size={32} />
      </div>
    )
  }

  return (
    <div style={{ maxWidth: 720 }}>
      <Typography.Title heading={4} style={{ marginTop: 0, marginBottom: 20 }}>
        Transcode Configuration
      </Typography.Title>

      {/* Enable Transcoding */}
      <SettingRow
        label="Enable Transcoding"
        description="When enabled, uploaded videos are transcoded to HLS multi-bitrate format for adaptive streaming."
      >
        <Switch
          checked={enabled}
          onChange={(v) => {
            setEnabled(v)
            setDirty(true)
          }}
        />
      </SettingRow>

      {/* Resolution Tiers */}
      <Card style={{ marginBottom: 12 }}>
        <Typography.Text style={{ fontWeight: 600, fontSize: 14 }}>
          Resolution Tiers
        </Typography.Text>
        <div style={{ marginTop: 4, marginBottom: 16 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Select which resolutions to encode. Unchecked tiers will be skipped.
          </Typography.Text>
        </div>
        <div style={{ display: 'flex', gap: 12 }}>
          {(['480p', '720p', '1080p'] as const).map((tier) => {
            const checked = tiers.includes(tier)
            const info = TIER_INFO[tier]
            return (
              <div
                key={tier}
                onClick={() => {
                  const next = checked ? tiers.filter((t) => t !== tier) : [...tiers, tier]
                  setTiers(next)
                  setDirty(true)
                }}
                style={{
                  flex: 1,
                  padding: '14px 16px',
                  borderRadius: 8,
                  border: `2px solid ${checked ? 'rgb(var(--primary-6))' : 'var(--color-border)'}`,
                  backgroundColor: checked ? 'var(--color-primary-light-1)' : 'var(--color-fill-1)',
                  cursor: 'pointer',
                  transition: 'all 0.2s',
                }}
              >
                <Space align="start">
                  <Checkbox checked={checked} />
                  <div>
                    <Typography.Text style={{ fontWeight: 600, fontSize: 14 }}>
                      {tier}
                    </Typography.Text>
                    <div style={{ marginTop: 4 }}>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {info.resolution} · {info.bitrate}
                      </Typography.Text>
                    </div>
                  </div>
                </Space>
              </div>
            )
          })}
        </div>
      </Card>

      {/* Video Encoder */}
      <Card style={{ marginBottom: 12 }}>
        <Typography.Text style={{ fontWeight: 600, fontSize: 14 }}>
          Video Encoder
        </Typography.Text>
        <Select
          value={encoder}
          onChange={(v) => {
            setEncoder(v)
            setDirty(true)
          }}
          style={{ width: '100%', marginTop: 12 }}
          size="large"
        >
          {ENCODER_OPTIONS.map((opt) => (
            <Select.Option key={opt.value} value={opt.value}>
              {opt.label}
            </Select.Option>
          ))}
        </Select>
        <div style={{ marginTop: 8 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Auto mode detects GPU hardware and falls back to CPU if unavailable.
          </Typography.Text>
        </div>
      </Card>

      {/* Encoding Preset */}
      <Card style={{ marginBottom: 12 }}>
        <Typography.Text style={{ fontWeight: 600, fontSize: 14 }}>
          Encoding Preset
        </Typography.Text>
        <Select
          value={preset}
          onChange={(v) => {
            setPreset(v)
            setDirty(true)
          }}
          style={{ width: '100%', marginTop: 12 }}
          size="large"
        >
          {PRESET_OPTIONS.map((opt) => (
            <Select.Option key={opt.value} value={opt.value}>
              {opt.label}
            </Select.Option>
          ))}
        </Select>
        <div style={{ marginTop: 8 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            For NVENC, presets are auto-mapped to p1-p7 equivalents.
          </Typography.Text>
        </div>
      </Card>

      {/* Parallel Tier Encoding */}
      <SettingRow
        label="Parallel Tier Encoding"
        description="Encode selected tiers simultaneously for faster transcoding."
      >
        <Switch
          checked={parallelTiers}
          onChange={(v) => {
            setParallelTiers(v)
            setDirty(true)
          }}
        />
      </SettingRow>

      {/* Save Button */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
        <Button
          type="primary"
          size="large"
          icon={<IconSave />}
          loading={updateSettings.isPending}
          disabled={!dirty}
          onClick={handleSave}
        >
          Save Changes
        </Button>
      </div>
    </div>
  )
}
