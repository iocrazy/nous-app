import { describe, it, expect } from 'vitest';
import {
  ACCEPT_ATTR,
  MAX_FILES_AT_ONCE,
  MAX_FILE_SIZE_BYTES,
  formatBytes,
  validateFileBatch,
} from './ChatAttachmentPicker.helpers';

describe('formatBytes', () => {
  it('uses B under 1 KB', () => {
    expect(formatBytes(0)).toBe('0B');
    expect(formatBytes(1023)).toBe('1023B');
  });

  it('uses KB at the kb boundary', () => {
    expect(formatBytes(1024)).toBe('1KB');
    expect(formatBytes(1024 * 500)).toBe('500KB');
  });

  it('uses MB above 1 MB', () => {
    expect(formatBytes(1024 * 1024)).toBe('1.0MB');
    expect(formatBytes(2.5 * 1024 * 1024)).toBe('2.5MB');
  });
});

describe('validateFileBatch', () => {
  it('accepts empty list', () => {
    expect(validateFileBatch([])).toBeNull();
  });

  it('accepts batch under all caps', () => {
    expect(
      validateFileBatch([
        { name: 'a.png', size: 1024 },
        { name: 'b.pdf', size: 5_000_000 },
      ]),
    ).toBeNull();
  });

  it('rejects batches exceeding MAX_FILES_AT_ONCE', () => {
    const files = Array.from({ length: MAX_FILES_AT_ONCE + 1 }, (_, i) => ({
      name: `f${i}.png`,
      size: 100,
    }));
    expect(validateFileBatch(files)).toMatch(/too many/i);
  });

  it('rejects oversized file with size formatting', () => {
    const err = validateFileBatch([
      { name: 'huge.mp4', size: MAX_FILE_SIZE_BYTES + 1 },
    ]);
    expect(err).toMatch(/huge\.mp4/);
    expect(err).toMatch(/>\s*50MB/i);
  });

  it('reports first offending file when multiple oversized', () => {
    const err = validateFileBatch([
      { name: 'first_too_big.mp4', size: MAX_FILE_SIZE_BYTES + 1 },
      { name: 'second_too_big.mp4', size: MAX_FILE_SIZE_BYTES + 2 },
    ]);
    expect(err).toMatch(/first_too_big/);
    expect(err).not.toMatch(/second_too_big/);
  });
});

describe('ACCEPT_ATTR', () => {
  it('includes the documented kinds', () => {
    for (const ext of ['.jpg', '.png', '.mp4', '.pdf', '.webm']) {
      expect(ACCEPT_ATTR).toContain(ext);
    }
  });

  it('does NOT include disallowed extensions', () => {
    for (const ext of ['.exe', '.sh', '.bat', '.zip']) {
      expect(ACCEPT_ATTR).not.toContain(ext);
    }
  });
});
