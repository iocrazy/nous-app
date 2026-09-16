/**
 * 一次窗口里回合都是怎么结束的（3c §6 稿三）。
 *
 * `turn_end_reasons` 是一张开放的表——runner 每加一个终止理由，这里就会多一个键。
 * 十个理由里只有五个值得一眼分辨，其余归 Other：一条分成十段的条读不出任何东西。
 *
 * 配色全用语义色 token（K1 全站配色重映射之后色相名不再表示状态）：
 *   completed → ok        一个跑完的回合
 *   awaiting_input → info **在等人**，不是失败——这是 Success tile 改口径的同一裁定
 *   error → danger        真的坏了
 *   interrupted → warn    被腰斩
 *   cancelled / Other → ink（muted）  用户自己叫停、或不值得单独一段的理由
 *
 * 零完成回合时说「还没有」，不画一条空条：空条与「全是 completed 的条」在视觉上
 * 是两件完全不同的事，长得一样就是在骗人。
 */
import React from 'react';
import { useTranslation } from 'react-i18next';

interface KnownReason {
  key: string;
  className: string;
  label: string;
}

/** 值得单独一段的五个。顺序即条上的顺序：好消息在左，坏消息在右。 */
const KNOWN: KnownReason[] = [
  { key: 'completed', className: 'bg-ok', label: 'Completed' },
  { key: 'awaiting_input', className: 'bg-info', label: 'Awaiting input' },
  { key: 'error', className: 'bg-danger', label: 'Error' },
  { key: 'interrupted', className: 'bg-warn', label: 'Interrupted' },
  { key: 'cancelled', className: 'bg-ink-400', label: 'Cancelled' },
];

export const TurnEndBreakdown: React.FC<{ reasons: Record<string, number> }> = ({
  reasons,
}) => {
  const { t } = useTranslation();
  const total = Object.values(reasons).reduce((a, b) => a + (b > 0 ? b : 0), 0);

  if (total === 0) {
    return (
      <p className="text-xs text-ink-500" data-testid="turn-end-empty">
        {t('aiUsage.turnEndEmpty', 'No finished runs yet')}
      </p>
    );
  }

  // 已知五段之外剩下的一切（paused / max_iterations / 明天才加的那个）。
  const other = total - KNOWN.reduce((a, k) => a + (reasons[k.key] || 0), 0);
  const segments = [
    ...KNOWN.map((k) => ({ ...k, count: reasons[k.key] || 0 })),
    { key: 'other', className: 'bg-ink-600', label: 'Other', count: other },
    // 没人撞上的理由不画零宽度的一段——那是一条画不出来的线，只会让 hover 乱跳。
  ].filter((s) => s.count > 0);

  return (
    <div className="space-y-2">
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-ink-800">
        {segments.map((s) => (
          <div
            key={s.key}
            data-testid={`turn-end-${s.key}`}
            className={s.className}
            style={{ width: `${(s.count / total) * 100}%` }}
            title={`${t(`aiUsage.turnEnd.${s.key}`, s.label)}: ${s.count}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-500">
        {segments.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${s.className}`} />
            {t(`aiUsage.turnEnd.${s.key}`, s.label)} · {s.count}
          </span>
        ))}
      </div>
    </div>
  );
};

export default TurnEndBreakdown;
