import { describe, expect, it } from 'vitest';
import {
  BLOCKED_EXTENSIONS,
  MAX_FILE_SIZE,
  validateFile,
} from './uploadValidation';

function makeFile(name: string, size: number): File {
  // Avoid actually allocating `size` bytes (tests would OOM at 500 MB).
  // validateFile only reads file.size, so override via defineProperty.
  const file = new File(['x'], name, { type: 'application/octet-stream' });
  Object.defineProperty(file, 'size', { value: size, configurable: true });
  return file;
}

describe('validateFile', () => {
  it('passes common media extensions', () => {
    expect(validateFile(makeFile('clip.mp4', 1000))).toBeNull();
    expect(validateFile(makeFile('photo.JPG', 1000))).toBeNull();
    expect(validateFile(makeFile('song.mp3', 1000))).toBeNull();
    expect(validateFile(makeFile('notes.pdf', 1000))).toBeNull();
  });

  it('rejects every blocked extension (case-insensitive)', () => {
    for (const ext of BLOCKED_EXTENSIONS) {
      const file = makeFile(`bad${ext.toUpperCase()}`, 1000);
      expect(validateFile(file)).toBe('invalidFileType');
    }
  });

  it('rejects files at or above MAX_FILE_SIZE + 1', () => {
    const tooBig = makeFile('big.mp4', MAX_FILE_SIZE + 1);
    expect(validateFile(tooBig)).toBe('fileTooLarge');
  });

  it('accepts files exactly at MAX_FILE_SIZE', () => {
    const exact = makeFile('edge.mp4', MAX_FILE_SIZE);
    expect(validateFile(exact)).toBeNull();
  });

  it('allows extensionless filenames', () => {
    const file = makeFile('README', 100);
    expect(validateFile(file)).toBeNull();
  });
});
