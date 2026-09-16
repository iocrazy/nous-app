/**
 * 一次窗口里回合都是怎么结束的（3c §6 稿三）。
 *
 * `turn_end_reasons` 是一张开放的表——runner 每加一个终止理由，这里就会多一个键。
 * 十几个理由里只有八个值得一眼分辨，其余归 Other：一条分成十几段的条读不出任何东西。
 *
 * 配色全用语义色 token（K1 全站配色重映射之后色相名不再表示状态）：
 *   completed → ok        一个跑完的回合
 *   awaiting_input → info **在等人**，不是失败——这是 Success tile 改口径的同一裁定
 *   error → danger        真的坏了
 *   dead → danger         进程没了（liveness 扫描器判死）——和 error 同级的坏消息
 *   heartbeat_lost → warn 心跳断了：常常是网络或卡死，不一定是崩溃
 *   stranded → warn       后端重启时它正在飞——运维事件，不是这个 agent 的错
 *   interrupted → warn    被腰斩
 *   cancelled / Other → ink（muted）  用户自己叫停、或不值得单独一段的理由
 *
 * 后三者是 3c 终审 I4 补上的：在此之前那三类终态连 `turn_end_reason` 都不写，
 * 于是**进程死掉、心跳丢失、重启被斩这三类根本不进成功率的分母**——曲线只在
 * 「跑完了但结局不是 completed」之间比较。归进 Other 等于把它们又藏起来一次。
 *
 * 零完成回合时说「还没有」，不画一条空条：空条与「全是 completed 的条」在视觉上
 * 是两件完全不同的事，长得一样就是在骗人。
 */
import React from 'react';
import { useTranslation } from 'react-i18next';

interface KnownReason {
  /** wire 上的名字（后端 `turn_end_reason` 的值），也是 `data-testid` 的后缀。 */
  key: string;
  /** 翻译 key 的末段。camelCase，与 wire 的 snake_case **刻意**不同——翻译 key
   *  的命名风格是我们的，不是后端的（CLAUDE.md 命名风格）。 */
  i18nKey: string;
  className: string;
  label: string;
}

/** 值得单独一段的八个。顺序即条上的顺序：好消息在左，坏消息在右。 */
const KNOWN: KnownReason[] = [
  { key: 'completed', i18nKey: 'completed', className: 'bg-ok', label: 'Completed' },
  {
    key: 'awaiting_input',
    i18nKey: 'awaitingInput',
    className: 'bg-info',
    label: 'Awaiting input',
  },
  { key: 'error', i18nKey: 'error', className: 'bg-danger', label: 'Error' },
  { key: 'dead', i18nKey: 'dead', className: 'bg-danger', label: 'Process died' },
  {
    key: 'heartbeat_lost',
    i18nKey: 'heartbeatLost',
    className: 'bg-warn',
    label: 'Heartbeat lost',
  },
  { key: 'stranded', i18nKey: 'stranded', className: 'bg-warn', label: 'Stranded' },
  {
    key: 'interrupted',
    i18nKey: 'interrupted',
    className: 'bg-warn',
    label: 'Interrupted',
  },
  { key: 'cancelled', i18nKey: 'cancelled', className: 'bg-ink-400', label: 'Cancelled' },
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

  // 已知八段之外剩下的一切（paused / max_iterations / 明天才加的那个）。
  const other = total - KNOWN.reduce((a, k) => a + (reasons[k.key] || 0), 0);
  const segments = [
    ...KNOWN.map((k) => ({ ...k, count: reasons[k.key] || 0 })),
    {
      key: 'other',
      i18nKey: 'other',
      className: 'bg-ink-600',
      label: 'Other',
      count: other,
      // 未知 wire 名绝不拼进翻译 key——那会去查一个不存在的条目，界面上直接冒出
      // `aiUsage.turnEnd.max_iterations` 这种原文。
    },
    // 没人撞上的理由不画零宽度的一段——那是一条画不出来的线，只会让 hover 乱跳。
  ].filter((s) => s.count > 0);

  return (
    <div className="space-y-2">
      {/* 条本身是几个没有文字的 div；读屏念它只会念出一串空元素，而同样的信息
          下面的图例已经用文字说过一遍了。 */}
      <div
        className="flex h-2 w-full overflow-hidden rounded-full bg-ink-800"
        aria-hidden="true"
      >
        {segments.map((s) => (
          <div
            key={s.key}
            data-testid={`turn-end-${s.key}`}
            className={s.className}
            style={{ width: `${(s.count / total) * 100}%` }}
            title={`${t(`aiUsage.turnEnd.${s.i18nKey}`, s.label)}: ${s.count}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-500">
        {segments.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${s.className}`} />
            {t(`aiUsage.turnEnd.${s.i18nKey}`, s.label)} · {s.count}
          </span>
        ))}
      </div>
    </div>
  );
};

export default TurnEndBreakdown;
