/**
 * Unit tests for the taskService format helpers.
 *
 * The legacy Celery API tests (getTaskStatus / cancelTask /
 * getActiveTasks / getWorkerStats / getQueueStats / getDownloadProgress
 * / pollTaskStatus / getTaskStatusText / getTaskStatusColor) were
 * deleted alongside their wrappers — those endpoints are gone.
 */

import { describe, expect, it } from 'vitest';
import { formatBytes, formatRelativeTime } from './taskService';

describe('formatters', () => {
  it('formatBytes 0 → 0 B', () => {
    expect(formatBytes(0)).toBe('0 B');
  });

  it('formatBytes scales to MB', () => {
    expect(formatBytes(1024 * 1024)).toBe('1 MB');
  });

  it('formatRelativeTime: just now for <1m', () => {
    const now = new Date().toISOString();
    expect(formatRelativeTime(now)).toBe('Just now');
  });

  it('formatRelativeTime: minutes ago', () => {
    const past = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    expect(formatRelativeTime(past)).toBe('5m ago');
  });

  it('formatRelativeTime: hours ago', () => {
    const past = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString();
    expect(formatRelativeTime(past)).toBe('3h ago');
  });

  it('formatRelativeTime: days ago', () => {
    const past = new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString();
    expect(formatRelativeTime(past)).toBe('2d ago');
  });
});
