// 生产事故回归（2026-08-03，/ai-library 整页白屏）：AILibrarySidebar 与
// AgentGalleryPage 同时消费 useAgentRuns，channel 名固定为
// `agent-runs-${userId}` —— supabase-js 对同名 topic 返回同一 channel 实例，
// 第二个消费者在第一个 subscribe() 之后再 .on() 直接 throw
// "cannot add `postgres_changes` callbacks … after `subscribe()`"，
// React Router 错误边界接管整页。次生弹：任一方卸载 removeChannel 会
// 杀掉另一方的订阅。两者的解法相同：channel topic 必须每个 hook 实例唯一。
import { describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

const channelNames: string[] = [];

vi.mock('../supabaseClient', () => ({
  getSupabaseClient: () => ({
    from: () => ({
      select: () => ({
        eq: () => ({
          eq: () => Promise.resolve({ data: [], error: null }),
        }),
      }),
    }),
    channel: (name: string) => {
      channelNames.push(name);
      const ch = {
        on: () => ch,
        subscribe: () => ch,
      };
      return ch;
    },
    removeChannel: () => Promise.resolve('ok'),
  }),
}));

import { useAgentRuns } from './useAgentRuns';

describe('useAgentRuns channel topic', () => {
  it('two simultaneous consumers never share a channel topic', () => {
    channelNames.length = 0;
    const a = renderHook(() => useAgentRuns('user-1'));
    const b = renderHook(() => useAgentRuns('user-1'));
    expect(channelNames).toHaveLength(2);
    expect(channelNames[0]).not.toBe(channelNames[1]);
    // 仍要按 user 区分（RLS 性能提示语义保留）
    expect(channelNames[0]).toContain('agent-runs-user-1');
    expect(channelNames[1]).toContain('agent-runs-user-1');
    a.unmount();
    b.unmount();
  });
});
