/**
 * Pure logic extracted from ChatAttachmentPicker for unit testing.
 */

export const MAX_FILES_AT_ONCE = 4;
export const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024;
export const ACCEPT_ATTR =
  '.jpg,.jpeg,.png,.gif,.webp,.bmp,.mp4,.mov,.webm,.mkv,.avi,.pdf';

export function formatBytes(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}

/**
 * Validate an array of files (typically a FileList converted to array)
 * against per-turn caps. Returns null on success or an error string for
 * the first violation found.
 */
export function validateFileBatch(files: { name: string; size: number }[]):
  | string
  | null {
  if (files.length > MAX_FILES_AT_ONCE) {
    // Soft cap — caller may slice instead of erroring; we just report.
    return `Too many files (${files.length} > ${MAX_FILES_AT_ONCE})`;
  }
  for (const f of files) {
    if (f.size > MAX_FILE_SIZE_BYTES) {
      return `${f.name} is too large (${formatBytes(f.size)} > 50MB)`;
    }
  }
  return null;
}
