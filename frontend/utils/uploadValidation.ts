/**
 * Pre-upload client-side validation.
 *
 * Extracted from ResourcesView so the same allow/block policy can be
 * shared across upload entry points (grid drop zone, dialog picker,
 * shortcut actions) without duplicating the constants.
 */

export const BLOCKED_EXTENSIONS: ReadonlySet<string> = new Set([
  '.exe',
  '.bat',
  '.cmd',
  '.msi',
  '.scr',
  '.pif',
  '.com',
  '.sh',
  '.bash',
  '.ps1',
  '.vbs',
  '.wsf',
  '.jar',
]);

export const MAX_FILE_SIZE = 500 * 1024 * 1024; // 500 MB

export type UploadValidationError = 'invalidFileType' | 'fileTooLarge';

/**
 * Validate a File against the block list and size cap.
 * Returns the i18n key for the error, or null on pass.
 */
export function validateFile(file: File): UploadValidationError | null {
  const parts = file.name.split('.');
  const ext = parts.length > 1 ? `.${parts.pop()!.toLowerCase()}` : '';
  if (ext && BLOCKED_EXTENSIONS.has(ext)) {
    return 'invalidFileType';
  }
  if (file.size > MAX_FILE_SIZE) {
    return 'fileTooLarge';
  }
  return null;
}
