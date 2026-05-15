/**
 * Task display formatters.
 *
 * Historical note: this file used to wrap the legacy Celery /tasks/* API
 * (getTaskStatus / getActiveTasks / getWorkerStats / getQueueStats /
 * pollTaskStatus / getDownloadProgress / etc). Those endpoints were
 * removed in PR-D7 and the wrappers had zero live callers — deleted in
 * the post-PR-283 cleanup. The format helpers stay because 17 live
 * components import them.
 */

/**
 * Format bytes to human readable string.
 */
export const formatBytes = (bytes: number): string => {
  if (bytes === 0) return '0 B';

  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

/**
 * Format an ISO-8601 timestamp as a coarse relative-time string
 * (Just now / Xm ago / Xh ago / Xd ago).
 */
export const formatRelativeTime = (dateString: string): string => {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;

  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;

  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
};
