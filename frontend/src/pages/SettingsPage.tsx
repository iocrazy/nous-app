import { useState } from 'react'
import { useAuthStore } from '@/stores/authStore'
import { User, Key, Bell, Database, Save, Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'

export default function SettingsPage() {
  const { user } = useAuthStore()
  const [isLoading, setIsLoading] = useState(false)
  const [settings, setSettings] = useState({
    username: user?.user_metadata?.username || '',
    notifications: true,
    autoDownload: true,
    downloadPath: '/app/videos',
  })

  const handleSave = async () => {
    setIsLoading(true)
    // Simulate save
    await new Promise((resolve) => setTimeout(resolve, 1000))
    setIsLoading(false)
    toast.success('设置已保存')
  }

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white">设置</h1>
        <p className="text-dark-400 mt-1">管理您的账户和应用设置</p>
      </div>

      {/* Profile Settings */}
      <div className="card p-6">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2 mb-4">
          <User className="w-5 h-5 text-primary-500" />
          个人信息
        </h2>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-dark-300 mb-2">
              邮箱
            </label>
            <input
              type="email"
              value={user?.email || ''}
              disabled
              className="input bg-dark-700 cursor-not-allowed"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-dark-300 mb-2">
              用户名
            </label>
            <input
              type="text"
              value={settings.username}
              onChange={(e) =>
                setSettings({ ...settings, username: e.target.value })
              }
              className="input"
              placeholder="设置用户名"
            />
          </div>
        </div>
      </div>

      {/* Security Settings */}
      <div className="card p-6">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2 mb-4">
          <Key className="w-5 h-5 text-yellow-500" />
          安全设置
        </h2>
        <div className="space-y-4">
          <button className="btn btn-secondary">修改密码</button>
          <p className="text-sm text-dark-400">
            上次登录: {new Date().toLocaleString()}
          </p>
        </div>
      </div>

      {/* Notification Settings */}
      <div className="card p-6">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2 mb-4">
          <Bell className="w-5 h-5 text-blue-500" />
          通知设置
        </h2>
        <div className="space-y-4">
          <label className="flex items-center justify-between cursor-pointer">
            <span className="text-dark-200">下载完成通知</span>
            <input
              type="checkbox"
              checked={settings.notifications}
              onChange={(e) =>
                setSettings({ ...settings, notifications: e.target.checked })
              }
              className="w-5 h-5 rounded border-dark-500 bg-dark-600 text-primary-500 focus:ring-primary-500"
            />
          </label>
          <label className="flex items-center justify-between cursor-pointer">
            <span className="text-dark-200">自动开始下载</span>
            <input
              type="checkbox"
              checked={settings.autoDownload}
              onChange={(e) =>
                setSettings({ ...settings, autoDownload: e.target.checked })
              }
              className="w-5 h-5 rounded border-dark-500 bg-dark-600 text-primary-500 focus:ring-primary-500"
            />
          </label>
        </div>
      </div>

      {/* Storage Settings */}
      <div className="card p-6">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2 mb-4">
          <Database className="w-5 h-5 text-green-500" />
          存储设置
        </h2>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-dark-300 mb-2">
              默认下载路径
            </label>
            <input
              type="text"
              value={settings.downloadPath}
              onChange={(e) =>
                setSettings({ ...settings, downloadPath: e.target.value })
              }
              className="input"
            />
          </div>
        </div>
      </div>

      {/* Save Button */}
      <button
        onClick={handleSave}
        disabled={isLoading}
        className="btn btn-primary gap-2"
      >
        {isLoading ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Save className="w-4 h-4" />
        )}
        保存设置
      </button>
    </div>
  )
}
