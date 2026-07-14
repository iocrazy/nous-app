import { describe, expect, it } from 'vitest';
import { plainTextToDoc, docToPlainText } from './tiptapPlainText';

const roundtrip = (t: string) => docToPlainText(plainTextToDoc(t));

describe('tiptapPlainText round-trip (byte fidelity)', () => {
  it('preserves a .env verbatim', () => {
    const t = 'API_KEY=abc123\n# comment with # and *stars*\nPORT=8080\n';
    expect(roundtrip(t)).toBe(t);
  });
  it('preserves JSON with braces, quotes, indentation', () => {
    const t = '{\n  "a": 1,\n  "b": ["x", "y"]\n}';
    expect(roundtrip(t)).toBe(t);
  });
  it('preserves markdown-special characters that WYSIWYG would eat', () => {
    const t = '## not a heading\n* not a bullet\n`code`\n\n\ntrailing blanks';
    expect(roundtrip(t)).toBe(t);
  });
  it('preserves an empty string', () => {
    expect(roundtrip('')).toBe('');
  });
  it('sets the code block language when given', () => {
    const doc = plainTextToDoc('x', 'json');
    expect(doc.content?.[0]?.attrs?.language).toBe('json');
  });
});
