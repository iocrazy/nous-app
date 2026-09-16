/**
 * 「现在在干什么」的一行（3c §4.1）。驾驶舱早有 "Now:"，聊天面板一直没有——正在跑的
 * 回合只有一个转圈。纯展示：`current` 由宿主算出，`null` = 回合结束，此时不画。
 *
 * 秒数取整显示。唯一的时间源 `useElapsedSeconds` 本身就是整秒跳的，写 `4.2s` 会让
 * 小数位恒为 `.0`——一个永远不动的数字假装自己有精度，比不写更坏。
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';

export interface RunStatusLineProps {
  current: { step: number; tool: string | null } | null;
  elapsedMs: number;
}

export const RunStatusLine: React.FC<RunStatusLineProps> = ({ current, elapsedMs }) => {
  const { t } = useTranslation();
  if (!current) return null;
  const parts = [t('trajectory.step', 'Step {{n}}', { n: current.step })];
  if (current.tool) parts.push(t('trajectory.running', 'Running {{tool}}…', { tool: current.tool }));
  parts.push(`${Math.floor(elapsedMs / 1000)}s`);
  return (
    <div className="ml-1 flex items-center gap-1.5 px-3 py-1 text-[11px] text-ink-500" data-testid="run-status-line">
      <Loader2 size={10} className="animate-spin shrink-0" />
      {parts.join(' · ')}
    </div>
  );
};

export default RunStatusLine;
