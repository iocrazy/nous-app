// frontend/components/TopicInspiration/parseModeDetect.test.ts
import { describe, it, expect } from 'vitest';
import { detectParseMode } from './parseModeDetect';

describe('detectParseMode', () => {
  it('detects single link', () => {
    const r = detectParseMode('https://v.douyin.com/abc/');
    expect(r.mode).toBe('single');
    expect(r.count).toBe(1);
  });

  it('detects batch by multiple lines', () => {
    const r = detectParseMode('https://a.com/1\nhttps://a.com/2\nhttps://a.com/3');
    expect(r.mode).toBe('batch');
    expect(r.count).toBe(3);
  });

  it('detects playlist by keyword', () => {
    const r = detectParseMode('https://music.example.com/playlist/123');
    expect(r.mode).toBe('playlist');
  });

  it('empty input is single with count 0', () => {
    expect(detectParseMode('   ').count).toBe(0);
  });
});
