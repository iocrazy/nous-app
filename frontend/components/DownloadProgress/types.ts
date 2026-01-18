// frontend/components/DownloadProgress/types.ts

export type DownloadStatus = 'pending' | 'downloading' | 'completed' | 'failed' | 'retrying';

export interface DownloadProgressProps {
  /** Progress percentage 0-100 */
  percent: number;
  /** Current status */
  status: DownloadStatus;
  /** Download speed string e.g. "2.5 MB/s" */
  speed?: string;
  /** Retry callback when failed */
  onRetry?: () => void;
  /** Size variant */
  size?: 'normal' | 'mini';
  /** Thumbnail URL for background */
  thumbnailUrl?: string;
  /** Retry count for display */
  retryCount?: number;
  /** Max retries */
  maxRetries?: number;
}

export type ProgressStyleType = 'neon' | 'wave';
