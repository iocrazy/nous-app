import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'
import { Eye, EyeOff, Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'

export default function LoginPage() {
  const [isSignUp, setIsSignUp] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [username, setUsername] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const { signIn, signUp, isLoading } = useAuthStore()
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!email || !password) {
      toast.error('请填写邮箱和密码')
      return
    }

    let result
    if (isSignUp) {
      result = await signUp(email, password, username || undefined)
    } else {
      result = await signIn(email, password)
    }

    if (result.success) {
      toast.success(isSignUp ? '注册成功' : '登录成功')
      navigate('/dashboard')
    } else {
      toast.error(result.error || '操作失败')
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-dark-900 px-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-white mb-2">
            <span className="text-primary-500">抖音</span>视频分析
          </h1>
          <p className="text-dark-400">视频爬取、下载与管理</p>
        </div>

        {/* Form Card */}
        <div className="card p-6">
          <h2 className="text-xl font-semibold text-white mb-6">
            {isSignUp ? '创建账号' : '登录账号'}
          </h2>

          <form onSubmit={handleSubmit} className="space-y-4">
            {isSignUp && (
              <div>
                <label className="block text-sm font-medium text-dark-300 mb-1">
                  用户名（可选）
                </label>
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="input"
                  placeholder="输入用户名"
                />
              </div>
            )}

            <div>
              <label className="block text-sm font-medium text-dark-300 mb-1">
                邮箱
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="input"
                placeholder="输入邮箱地址"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-dark-300 mb-1">
                密码
              </label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="input pr-10"
                  placeholder="输入密码"
                  required
                  minLength={6}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-dark-400 hover:text-dark-200"
                >
                  {showPassword ? (
                    <EyeOff className="w-5 h-5" />
                  ) : (
                    <Eye className="w-5 h-5" />
                  )}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={isLoading}
              className="w-full btn btn-primary h-11"
            >
              {isLoading ? (
                <Loader2 className="w-5 h-5 animate-spin" />
              ) : isSignUp ? (
                '注册'
              ) : (
                '登录'
              )}
            </button>
          </form>

          <div className="mt-6 text-center">
            <button
              onClick={() => setIsSignUp(!isSignUp)}
              className="text-sm text-primary-400 hover:text-primary-300"
            >
              {isSignUp ? '已有账号？点击登录' : '没有账号？点击注册'}
            </button>
          </div>
        </div>

        {/* Footer */}
        <p className="mt-8 text-center text-sm text-dark-500">
          Powered by Supabase & FastAPI
        </p>
      </div>
    </div>
  )
}
