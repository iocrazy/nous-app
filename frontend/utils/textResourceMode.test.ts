import { describe, expect, it } from 'vitest';
import {
  classifyTextResource,
  codeLangForExtension,
  TEXT_EDIT_MAX_BYTES,
} from './textResourceMode';

describe('classifyTextResource', () => {
  it('classifies markdown by extension', () => {
    expect(classifyTextResource({ filename: 'a.md', mime: '', sizeBytes: 100 })).toBe('markdown');
    expect(classifyTextResource({ filename: 'a.markdown', mime: null, sizeBytes: 100 })).toBe('markdown');
  });
  it('classifies markdown by mime', () => {
    expect(classifyTextResource({ filename: 'x', mime: 'text/markdown', sizeBytes: 10 })).toBe('markdown');
  });
  it('classifies non-markdown text as plaintext', () => {
    for (const f of ['a.env', 'b.json', 'c.log', 'd.txt', 'e.py', 'f.yaml']) {
      expect(classifyTextResource({ filename: f, mime: '', sizeBytes: 10 })).toBe('plaintext');
    }
  });
  it('classifies text/* mime with unknown extension as plaintext', () => {
    expect(classifyTextResource({ filename: 'noext', mime: 'text/plain', sizeBytes: 10 })).toBe('plaintext');
  });
  it('returns null for non-text mime with unknown extension', () => {
    expect(classifyTextResource({ filename: 'blob.bin', mime: 'application/octet-stream', sizeBytes: 10 })).toBeNull();
    expect(classifyTextResource({ filename: 'p.png', mime: 'image/png', sizeBytes: 10 })).toBeNull();
  });
  it('flags oversize text', () => {
    expect(classifyTextResource({ filename: 'big.md', mime: '', sizeBytes: TEXT_EDIT_MAX_BYTES + 1 })).toBe('oversize');
    expect(classifyTextResource({ filename: 'big.json', mime: '', sizeBytes: TEXT_EDIT_MAX_BYTES + 1 })).toBe('oversize');
  });
});

describe('codeLangForExtension', () => {
  it('maps known extensions to lowlight language names', () => {
    expect(codeLangForExtension('json')).toBe('json');
    expect(codeLangForExtension('yaml')).toBe('yaml');
    expect(codeLangForExtension('yml')).toBe('yaml');
    expect(codeLangForExtension('py')).toBe('python');
    expect(codeLangForExtension('js')).toBe('javascript');
    expect(codeLangForExtension('ts')).toBe('typescript');
  });
  it('returns null for unknown extensions', () => {
    expect(codeLangForExtension('env')).toBeNull();
    expect(codeLangForExtension('zzz')).toBeNull();
  });
});
