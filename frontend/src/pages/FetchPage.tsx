import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { douyinApi } from '@/lib/api'
import { Download, Link, Music, Video, Loader2, CheckCircle, AlertCircle } from 'lucide-react'
import toast from 'react-hot-toast'
import { cn } from '@/lib/utils'

type FetchResult = {
  success: boolean
  aweme_id?: string
  video_title?: string
  author?: string
  message?: string
}

export default function FetchPage() {
  const [url, setUrl] = useState('')
  const [urls, setUrls] = useState('')
  const [downloadVideo, setDownloadVideo] = useState(true)
  const [downloadMusic, setDownloadMusic] = useState(false)
  const [categories, setCategories] = useState('')
  const [isBatchMode, setIsBatchMode] = useState(false)
  const [results, setResults] = useState<FetchResult[]>([])

  const fetchMutation = useMutation({
    mutationFn: (videoUrl: string) =>
      douyinApi.fetchVideo(videoUrl, {
        video: downloadVideo,
        music: downloadMusic,
        categories: categories || undefined,
      }),
    onSuccess: (data) => {
      setResults((prev) => [
        {
          success: true,
          aweme_id: data.aweme_id,
          video_title: data.video_title,
          author: data.author,
          message: data.message,
        },
        ...prev,
      ])
      toast.success('视频获取成功')
      setUrl('')
    },
    onError: (error: Error) => {
      setResults((prev) => [
        { success: false, message: error.message },
        ...prev,
      ])
      toast.error('获取失败: ' + error.message)
    },
  })

  const batchMutation = useMutation({
    mutationFn: (videoUrls: string[]) =>
      douyinApi.fetchBatch(videoUrls, {
        video: downloadVideo,
        music: downloadMusic,
        categories: categories || undefined,
      }),
    onSuccess: (data) => {
      const newResults: FetchResult[] = []
      data.results?.forEach((r: { aweme_id: string; status: string }) => {
        newResults.push({
          success: true,
          aweme_id: r.aweme_id,
          message: r.status,
        })
      })
      data.errors?.forEach((e: { url: string; error: string }) => {
        newResults.push({
          success: false,
          message: `${e.url}: ${e.error}`,
        })
      })
      setResults((prev) => [...newResults, ...prev])
      toast.success(`批量获取完成: ${data.submitted} 成功, ${data.failed} 失败`)
      setUrls('')
    },
    onError: (error: Error) => {
      toast.error('批量获取失败: ' + error.message)
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (isBatchMode) {
      const urlList = urls
        .split('\n')
        .map((u) => u.trim())
        .filter((u) => u)
      if (urlList.length === 0) {
        toast.error('请输入至少一个链接')
        return
      }
      batchMutation.mutate(urlList)
    } else {
      if (!url.trim()) {
        toast.error('请输入视频链接')
        return
      }
      fetchMutation.mutate(url.trim())
    }
  }

  const isLoading = fetchMutation.isPending || batchMutation.isPending

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white">获取视频</h1>
        <p className="text-dark-400 mt-1">输入抖音链接获取视频信息并下载</p>
      </div>

      {/* Form */}
      <div className="card p-6">
        {/* Mode Toggle */}
        <div className="flex gap-2 mb-6">
          <button
            onClick={() => setIsBatchMode(false)}
            className={cn(
              'btn',
              !isBatchMode ? 'btn-primary' : 'btn-secondary'
            )}
          >
            单个获取
          </button>
          <button
            onClick={() => setIsBatchMode(true)}
            className={cn('btn', isBatchMode ? 'btn-primary' : 'btn-secondary')}
          >
            批量获取
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* URL Input */}
          {isBatchMode ? (
            <div>
              <label className="block text-sm font-medium text-dark-300 mb-2">
                视频链接（每行一个）
              </label>
              <textarea
                value={urls}
                onChange={(e) => setUrls(e.target.value)}
                className="input min-h-[150px] resize-y"
                placeholder="https://v.douyin.com/xxx&#10;https://v.douyin.com/yyy&#10;https://v.douyin.com/zzz"
              />
            </div>
          ) : (
            <div>
              <label className="block text-sm font-medium text-dark-300 mb-2">
                视频链接
              </label>
              <div className="relative">
                <Link className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-dark-400" />
                <input
                  type="text"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  className="input pl-10"
                  placeholder="输入抖音分享链接"
                />
              </div>
            </div>
          )}

          {/* Options */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="flex items-center gap-3 p-4 bg-dark-700 rounded-lg">
              <input
                type="checkbox"
                id="downloadVideo"
                checked={downloadVideo}
                onChange={(e) => setDownloadVideo(e.target.checked)}
                className="w-5 h-5 rounded border-dark-500 bg-dark-600 text-primary-500 focus:ring-primary-500"
              />
              <label
                htmlFor="downloadVideo"
                className="flex items-center gap-2 cursor-pointer"
              >
                <Video className="w-5 h-5 text-blue-500" />
                <span className="text-dark-200">下载视频/图片</span>
              </label>
            </div>

            <div className="flex items-center gap-3 p-4 bg-dark-700 rounded-lg">
              <input
                type="checkbox"
                id="downloadMusic"
                checked={downloadMusic}
                onChange={(e) => setDownloadMusic(e.target.checked)}
                className="w-5 h-5 rounded border-dark-500 bg-dark-600 text-primary-500 focus:ring-primary-500"
              />
              <label
                htmlFor="downloadMusic"
                className="flex items-center gap-2 cursor-pointer"
              >
                <Music className="w-5 h-5 text-green-500" />
                <span className="text-dark-200">下载音频</span>
              </label>
            </div>
          </div>

          {/* Categories */}
          <div>
            <label className="block text-sm font-medium text-dark-300 mb-2">
              分类标签（可选）
            </label>
            <input
              type="text"
              value={categories}
              onChange={(e) => setCategories(e.target.value)}
              className="input"
              placeholder="例如: 美食, 教程"
            />
          </div>

          {/* Submit */}
          <button
            type="submit"
            disabled={isLoading}
            className="w-full btn btn-primary h-12 gap-2"
          >
            {isLoading ? (
              <>
                <Loader2 className="w-5 h-5 animate-spin" />
                获取中...
              </>
            ) : (
              <>
                <Download className="w-5 h-5" />
                {isBatchMode ? '批量获取' : '获取视频'}
              </>
            )}
          </button>
        </form>
      </div>

      {/* Results */}
      {results.length > 0 && (
        <div className="card p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">获取结果</h2>
            <button
              onClick={() => setResults([])}
              className="text-sm text-dark-400 hover:text-dark-200"
            >
              清空
            </button>
          </div>
          <div className="space-y-3 max-h-96 overflow-y-auto">
            {results.map((result, index) => (
              <div
                key={index}
                className={cn(
                  'p-4 rounded-lg border',
                  result.success
                    ? 'bg-green-500/10 border-green-500/30'
                    : 'bg-red-500/10 border-red-500/30'
                )}
              >
                <div className="flex items-start gap-3">
                  {result.success ? (
                    <CheckCircle className="w-5 h-5 text-green-500 flex-shrink-0 mt-0.5" />
                  ) : (
                    <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
                  )}
                  <div className="flex-1 min-w-0">
                    {result.success ? (
                      <>
                        <p className="text-sm font-medium text-white truncate">
                          {result.video_title || result.aweme_id}
                        </p>
                        {result.author && (
                          <p className="text-xs text-dark-400">作者: {result.author}</p>
                        )}
                        <p className="text-xs text-green-400 mt-1">
                          {result.message || '获取成功'}
                        </p>
                      </>
                    ) : (
                      <p className="text-sm text-red-400">{result.message}</p>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
