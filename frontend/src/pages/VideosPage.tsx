import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { douyinApi } from '@/lib/api'
import {
  Search,
  RefreshCw,
  Trash2,
  ExternalLink,
  ChevronLeft,
  ChevronRight,
  Filter,
} from 'lucide-react'
import {
  cn,
  formatNumber,
  getStatusColor,
  getStatusText,
  getAwemeTypeText,
} from '@/lib/utils'
import toast from 'react-hot-toast'
import type { Video } from '@/lib/supabase'

const PAGE_SIZE = 20

export default function VideosPage() {
  const [page, setPage] = useState(0)
  const [searchKeyword, setSearchKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const queryClient = useQueryClient()

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['videos', page, searchKeyword, statusFilter],
    queryFn: () => {
      if (searchKeyword || statusFilter) {
        return douyinApi.searchVideos({
          keyword: searchKeyword || undefined,
          status: statusFilter || undefined,
          skip: page * PAGE_SIZE,
          limit: PAGE_SIZE,
        })
      }
      return douyinApi.getVideos({
        skip: page * PAGE_SIZE,
        limit: PAGE_SIZE,
      })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (awemeId: string) => douyinApi.deleteVideo(awemeId),
    onSuccess: () => {
      toast.success('删除成功')
      queryClient.invalidateQueries({ queryKey: ['videos'] })
    },
    onError: () => {
      toast.error('删除失败')
    },
  })

  const retryMutation = useMutation({
    mutationFn: (awemeId: string) => douyinApi.retryDownload(awemeId),
    onSuccess: () => {
      toast.success('重试任务已提交')
      queryClient.invalidateQueries({ queryKey: ['videos'] })
    },
    onError: () => {
      toast.error('重试失败')
    },
  })

  const videos: Video[] = data?.videos || []

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    setPage(0)
    refetch()
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white">视频管理</h1>
          <p className="text-dark-400 mt-1">管理所有抓取的视频</p>
        </div>
        <button onClick={() => refetch()} className="btn btn-secondary gap-2">
          <RefreshCw className="w-4 h-4" />
          刷新
        </button>
      </div>

      {/* Filters */}
      <div className="card p-4">
        <form onSubmit={handleSearch} className="flex flex-col sm:flex-row gap-4">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-dark-400" />
            <input
              type="text"
              value={searchKeyword}
              onChange={(e) => setSearchKeyword(e.target.value)}
              className="input pl-10"
              placeholder="搜索标题、作者..."
            />
          </div>
          <div className="flex gap-3">
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value)
                setPage(0)
              }}
              className="input w-auto"
            >
              <option value="">全部状态</option>
              <option value="pending">待下载</option>
              <option value="completed">已完成</option>
              <option value="failed">失败</option>
              <option value="skipped">已跳过</option>
            </select>
            <button type="submit" className="btn btn-primary">
              <Filter className="w-4 h-4" />
            </button>
          </div>
        </form>
      </div>

      {/* Table */}
      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-dark-700/50">
              <tr>
                <th className="px-4 py-3 text-left text-sm font-medium text-dark-300">
                  视频信息
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-dark-300">
                  作者
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-dark-300">
                  类型
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-dark-300">
                  互动数据
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-dark-300">
                  状态
                </th>
                <th className="px-4 py-3 text-right text-sm font-medium text-dark-300">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-dark-700">
              {isLoading ? (
                [...Array(5)].map((_, i) => (
                  <tr key={i}>
                    <td colSpan={6} className="px-4 py-4">
                      <div className="animate-pulse flex gap-4">
                        <div className="h-12 w-12 bg-dark-700 rounded" />
                        <div className="flex-1 space-y-2">
                          <div className="h-4 bg-dark-700 rounded w-3/4" />
                          <div className="h-3 bg-dark-700 rounded w-1/2" />
                        </div>
                      </div>
                    </td>
                  </tr>
                ))
              ) : videos.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-12 text-center text-dark-400">
                    暂无数据
                  </td>
                </tr>
              ) : (
                videos.map((video) => (
                  <tr key={video.id} className="hover:bg-dark-700/30">
                    <td className="px-4 py-4">
                      <div className="flex items-start gap-3 max-w-xs">
                        <div className="w-12 h-12 bg-dark-700 rounded flex-shrink-0" />
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-white truncate">
                            {video.video_title || '无标题'}
                          </p>
                          <p className="text-xs text-dark-400 truncate">
                            {video.aweme_id}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-4">
                      <span className="text-sm text-dark-200">
                        {video.author || '-'}
                      </span>
                    </td>
                    <td className="px-4 py-4">
                      <span className="badge bg-dark-600 text-dark-200">
                        {getAwemeTypeText(video.aweme_type)}
                      </span>
                    </td>
                    <td className="px-4 py-4">
                      <div className="text-xs text-dark-400 space-y-1">
                        <p>点赞: {formatNumber(video.video_digg_count || 0)}</p>
                        <p>评论: {formatNumber(video.video_comment_count || 0)}</p>
                      </div>
                    </td>
                    <td className="px-4 py-4">
                      <span
                        className={cn(
                          'badge',
                          getStatusColor(video.video_download_status)
                        )}
                      >
                        {getStatusText(video.video_download_status)}
                      </span>
                    </td>
                    <td className="px-4 py-4">
                      <div className="flex items-center justify-end gap-2">
                        <a
                          href={video.video_original_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="btn btn-ghost p-2"
                          title="打开原链接"
                        >
                          <ExternalLink className="w-4 h-4" />
                        </a>
                        {video.video_download_status === 'failed' && (
                          <button
                            onClick={() => retryMutation.mutate(video.aweme_id)}
                            className="btn btn-ghost p-2 text-yellow-500"
                            title="重试下载"
                          >
                            <RefreshCw className="w-4 h-4" />
                          </button>
                        )}
                        <button
                          onClick={() => {
                            if (confirm('确定要删除这条记录吗？')) {
                              deleteMutation.mutate(video.aweme_id)
                            }
                          }}
                          className="btn btn-ghost p-2 text-red-500"
                          title="删除"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="flex items-center justify-between px-4 py-3 border-t border-dark-700">
          <p className="text-sm text-dark-400">
            显示 {videos.length} 条记录
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="btn btn-ghost p-2"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>
            <span className="text-sm text-dark-300">第 {page + 1} 页</span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={videos.length < PAGE_SIZE}
              className="btn btn-ghost p-2"
            >
              <ChevronRight className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
