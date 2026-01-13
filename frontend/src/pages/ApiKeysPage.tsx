import { useState, useEffect } from 'react'
import { apiKeyApi } from '@/lib/api'
import type { ApiKey, ApiKeyScope, ApiKeyCreateRequest } from '@/types/api-key'
import {
  Key,
  Plus,
  Copy,
  Trash2,
  Ban,
  Loader2,
  Check,
  X,
  Eye,
  EyeOff,
  Clock,
  Shield,
} from 'lucide-react'
import toast from 'react-hot-toast'

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKey[]>([])
  const [scopes, setScopes] = useState<ApiKeyScope[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [showSecretModal, setShowSecretModal] = useState(false)
  const [newSecretKey, setNewSecretKey] = useState('')
  const [includeRevoked, setIncludeRevoked] = useState(false)

  // Load keys and scopes
  useEffect(() => {
    loadData()
  }, [includeRevoked])

  const loadData = async () => {
    setIsLoading(true)
    try {
      const [keysRes, scopesRes] = await Promise.all([
        apiKeyApi.list(includeRevoked),
        apiKeyApi.getScopes(),
      ])
      setKeys(keysRes.keys)
      setScopes(scopesRes.scopes)
    } catch (error) {
      toast.error('加载数据失败')
    } finally {
      setIsLoading(false)
    }
  }

  const handleCreate = async (data: ApiKeyCreateRequest) => {
    try {
      const result = await apiKeyApi.create(data)
      setNewSecretKey(result.secret_key)
      setShowCreateModal(false)
      setShowSecretModal(true)
      loadData()
      toast.success('API 密钥创建成功')
    } catch (error: any) {
      toast.error(error.response?.data?.detail || '创建失败')
    }
  }

  const handleRevoke = async (keyId: string) => {
    if (!confirm('确定要撤销此密钥吗？撤销后将无法使用。')) return
    try {
      await apiKeyApi.revoke(keyId)
      loadData()
      toast.success('密钥已撤销')
    } catch (error) {
      toast.error('撤销失败')
    }
  }

  const handleDelete = async (keyId: string) => {
    if (!confirm('确定要删除此密钥吗？此操作不可恢复。')) return
    try {
      await apiKeyApi.delete(keyId)
      loadData()
      toast.success('密钥已删除')
    } catch (error) {
      toast.error('删除失败')
    }
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    toast.success('已复制到剪贴板')
  }

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '-'
    return new Date(dateStr).toLocaleString('zh-CN')
  }

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'active':
        return (
          <span className="px-2 py-1 text-xs rounded-full bg-green-500/20 text-green-400">
            活跃
          </span>
        )
      case 'revoked':
        return (
          <span className="px-2 py-1 text-xs rounded-full bg-red-500/20 text-red-400">
            已撤销
          </span>
        )
      case 'expired':
        return (
          <span className="px-2 py-1 text-xs rounded-full bg-yellow-500/20 text-yellow-400">
            已过期
          </span>
        )
      default:
        return null
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Key className="w-6 h-6 text-primary-500" />
            API 密钥管理
          </h1>
          <p className="text-dark-400 mt-1">
            创建和管理用于 API 访问的密钥
          </p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="btn btn-primary gap-2"
        >
          <Plus className="w-4 h-4" />
          创建密钥
        </button>
      </div>

      {/* Filter */}
      <div className="card p-4">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={includeRevoked}
            onChange={(e) => setIncludeRevoked(e.target.checked)}
            className="w-4 h-4 rounded border-dark-500 bg-dark-600 text-primary-500"
          />
          <span className="text-dark-300">显示已撤销的密钥</span>
        </label>
      </div>

      {/* Keys List */}
      {isLoading ? (
        <div className="card p-12 flex items-center justify-center">
          <Loader2 className="w-8 h-8 animate-spin text-primary-500" />
        </div>
      ) : keys.length === 0 ? (
        <div className="card p-12 text-center">
          <Key className="w-12 h-12 text-dark-500 mx-auto mb-4" />
          <p className="text-dark-400">暂无 API 密钥</p>
          <p className="text-dark-500 text-sm mt-1">
            创建一个密钥来通过 API 访问您的数据
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {keys.map((key) => (
            <div key={key.key_id} className="card p-6">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-3">
                    <h3 className="text-lg font-semibold text-white">
                      {key.name}
                    </h3>
                    {getStatusBadge(key.status)}
                  </div>
                  <p className="text-dark-400 text-sm mt-1">
                    {key.description || '无描述'}
                  </p>
                  <div className="mt-3 flex items-center gap-2">
                    <code className="px-3 py-1 bg-dark-700 rounded text-dark-300 text-sm font-mono">
                      {key.key_prefix}
                    </code>
                    <button
                      onClick={() => copyToClipboard(key.key_id)}
                      className="p-1 hover:bg-dark-600 rounded transition-colors"
                      title="复制 Key ID"
                    >
                      <Copy className="w-4 h-4 text-dark-400" />
                    </button>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {key.status === 'active' && (
                    <button
                      onClick={() => handleRevoke(key.key_id)}
                      className="p-2 hover:bg-dark-600 rounded transition-colors"
                      title="撤销密钥"
                    >
                      <Ban className="w-5 h-5 text-yellow-500" />
                    </button>
                  )}
                  <button
                    onClick={() => handleDelete(key.key_id)}
                    className="p-2 hover:bg-dark-600 rounded transition-colors"
                    title="删除密钥"
                  >
                    <Trash2 className="w-5 h-5 text-red-500" />
                  </button>
                </div>
              </div>

              {/* Scopes */}
              <div className="mt-4">
                <p className="text-sm text-dark-400 mb-2">权限范围:</p>
                <div className="flex flex-wrap gap-2">
                  {key.scopes.map((scope) => (
                    <span
                      key={scope}
                      className="px-2 py-1 text-xs rounded bg-primary-500/20 text-primary-400"
                    >
                      {scope}
                    </span>
                  ))}
                </div>
              </div>

              {/* Stats */}
              <div className="mt-4 pt-4 border-t border-dark-600 grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                  <p className="text-dark-500">创建时间</p>
                  <p className="text-dark-300">{formatDate(key.created_at)}</p>
                </div>
                <div>
                  <p className="text-dark-500">最后使用</p>
                  <p className="text-dark-300">
                    {formatDate(key.last_used_at)}
                  </p>
                </div>
                <div>
                  <p className="text-dark-500">使用次数</p>
                  <p className="text-dark-300">{key.usage_count}</p>
                </div>
                <div>
                  <p className="text-dark-500">过期时间</p>
                  <p className="text-dark-300">
                    {key.expires_at ? formatDate(key.expires_at) : '永不过期'}
                  </p>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <CreateKeyModal
          scopes={scopes}
          onClose={() => setShowCreateModal(false)}
          onCreate={handleCreate}
        />
      )}

      {/* Secret Key Modal */}
      {showSecretModal && (
        <SecretKeyModal
          secretKey={newSecretKey}
          onClose={() => {
            setShowSecretModal(false)
            setNewSecretKey('')
          }}
        />
      )}
    </div>
  )
}

// Create Key Modal
function CreateKeyModal({
  scopes,
  onClose,
  onCreate,
}: {
  scopes: ApiKeyScope[]
  onClose: () => void
  onCreate: (data: ApiKeyCreateRequest) => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [selectedScopes, setSelectedScopes] = useState<string[]>([])
  const [expiresIn, setExpiresIn] = useState<string>('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name || selectedScopes.length === 0) {
      toast.error('请填写名称并选择至少一个权限')
      return
    }

    setIsSubmitting(true)
    let expires_at: string | undefined
    if (expiresIn) {
      const days = parseInt(expiresIn)
      const date = new Date()
      date.setDate(date.getDate() + days)
      expires_at = date.toISOString()
    }

    await onCreate({
      name,
      description: description || undefined,
      scopes: selectedScopes,
      expires_at,
    })
    setIsSubmitting(false)
  }

  const toggleScope = (scope: string) => {
    if (selectedScopes.includes(scope)) {
      setSelectedScopes(selectedScopes.filter((s) => s !== scope))
    } else {
      setSelectedScopes([...selectedScopes, scope])
    }
  }

  // Group scopes by category
  const scopesByCategory = scopes.reduce(
    (acc, scope) => {
      if (!acc[scope.category]) {
        acc[scope.category] = []
      }
      acc[scope.category].push(scope)
      return acc
    },
    {} as Record<string, ApiKeyScope[]>
  )

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="card w-full max-w-lg max-h-[90vh] overflow-y-auto m-4">
        <div className="p-6">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-xl font-bold text-white">创建 API 密钥</h2>
            <button
              onClick={onClose}
              className="p-1 hover:bg-dark-600 rounded"
            >
              <X className="w-5 h-5 text-dark-400" />
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-dark-300 mb-2">
                名称 *
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="input"
                placeholder="例如：自动化脚本"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-dark-300 mb-2">
                描述
              </label>
              <input
                type="text"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="input"
                placeholder="可选描述"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-dark-300 mb-2">
                有效期
              </label>
              <select
                value={expiresIn}
                onChange={(e) => setExpiresIn(e.target.value)}
                className="input"
              >
                <option value="">永不过期</option>
                <option value="7">7 天</option>
                <option value="30">30 天</option>
                <option value="90">90 天</option>
                <option value="365">1 年</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-dark-300 mb-2">
                权限范围 *
              </label>
              <div className="space-y-4">
                {Object.entries(scopesByCategory).map(
                  ([category, categoryScopes]) => (
                    <div key={category}>
                      <p className="text-sm text-dark-400 mb-2">{category}</p>
                      <div className="space-y-2">
                        {categoryScopes.map((scope) => (
                          <label
                            key={scope.scope}
                            className="flex items-start gap-3 p-3 bg-dark-700 rounded cursor-pointer hover:bg-dark-600 transition-colors"
                          >
                            <input
                              type="checkbox"
                              checked={selectedScopes.includes(scope.scope)}
                              onChange={() => toggleScope(scope.scope)}
                              className="mt-0.5 w-4 h-4 rounded border-dark-500 bg-dark-600 text-primary-500"
                            />
                            <div>
                              <p className="text-dark-200 font-medium">
                                {scope.name}
                              </p>
                              <p className="text-dark-400 text-sm">
                                {scope.description}
                              </p>
                              <code className="text-xs text-dark-500 mt-1 block">
                                {scope.scope}
                              </code>
                            </div>
                          </label>
                        ))}
                      </div>
                    </div>
                  )
                )}
              </div>
            </div>

            <div className="flex justify-end gap-3 pt-4">
              <button
                type="button"
                onClick={onClose}
                className="btn btn-secondary"
              >
                取消
              </button>
              <button
                type="submit"
                disabled={isSubmitting}
                className="btn btn-primary gap-2"
              >
                {isSubmitting ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Key className="w-4 h-4" />
                )}
                创建密钥
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}

// Secret Key Modal
function SecretKeyModal({
  secretKey,
  onClose,
}: {
  secretKey: string
  onClose: () => void
}) {
  const [showKey, setShowKey] = useState(false)
  const [copied, setCopied] = useState(false)

  const copyKey = () => {
    navigator.clipboard.writeText(secretKey)
    setCopied(true)
    toast.success('密钥已复制')
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="card w-full max-w-lg m-4">
        <div className="p-6">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-3 bg-green-500/20 rounded-full">
              <Check className="w-6 h-6 text-green-500" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-white">密钥创建成功</h2>
              <p className="text-dark-400 text-sm">
                请立即复制保存，此密钥仅显示一次！
              </p>
            </div>
          </div>

          <div className="bg-dark-700 rounded-lg p-4 mb-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm text-dark-400">您的 API 密钥</span>
              <button
                onClick={() => setShowKey(!showKey)}
                className="p-1 hover:bg-dark-600 rounded"
              >
                {showKey ? (
                  <EyeOff className="w-4 h-4 text-dark-400" />
                ) : (
                  <Eye className="w-4 h-4 text-dark-400" />
                )}
              </button>
            </div>
            <code className="block text-sm font-mono text-primary-400 break-all">
              {showKey ? secretKey : '•'.repeat(secretKey.length)}
            </code>
          </div>

          <div className="bg-yellow-500/10 border border-yellow-500/20 rounded-lg p-4 mb-6">
            <div className="flex items-start gap-3">
              <Shield className="w-5 h-5 text-yellow-500 flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-yellow-400 font-medium">重要提示</p>
                <p className="text-dark-300 text-sm mt-1">
                  此密钥仅显示一次，关闭后无法再次查看。请妥善保存到安全的地方。
                </p>
              </div>
            </div>
          </div>

          <div className="flex justify-end gap-3">
            <button onClick={copyKey} className="btn btn-secondary gap-2">
              {copied ? (
                <Check className="w-4 h-4" />
              ) : (
                <Copy className="w-4 h-4" />
              )}
              {copied ? '已复制' : '复制密钥'}
            </button>
            <button onClick={onClose} className="btn btn-primary">
              完成
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
