/**
 * 一个产出版本的花费，四个面共用一份写法（线程卡 / 右栏产出行 / 差异弹层两侧头 / 来源块）。
 * 分开写四遍的结果必然是四种精度和四种「没有价」的说法。
 *
 * `allocated` 带 `≈`：文本类花费是读时从 `step_end` 按该 (turn, step) 下的登记行数
 * 均摊出来的（3b §3.1），是参考值不是计费输入 —— 显示得跟目录价一样精确，就是在
 * 邀请别人拿它对账。
 */
export type CostKind = 'allocated' | 'exact' | null;

const cents = (c: number): string => `¢${c < 0.01 ? c.toFixed(3) : c.toFixed(2)}`;

/** The smallest price three decimals can show without rounding to zero. */
const FLOOR = 0.0005;

export function formatOutputCost(c: number | null, kind: CostKind): string {
  if (c === null || c === 0) return '—';
  // `¢0.000` states the work was free — the one thing we know is false here.
  // `<` already carries the imprecision, so no `≈` is stacked on top of it.
  if (c < FLOOR) return '<¢0.001';
  return kind === 'allocated' ? `≈${cents(c)}` : cents(c);
}

export function outputCostTitle(
  c: number | null,
  kind: CostKind,
  o: { deliverableKind: string; model: string | null },
): string | undefined {
  if (kind === 'allocated') return 'Allocated from step cost';
  // 只有媒体类能断言「该有价却没有」：它的价来自 ai_model_prices 的目录行，缺行就是缺配置。
  // 文本类没值只说明这一步的 step_end 还没到。
  if ((c === null || c === 0) && o.deliverableKind === 'generated_media') {
    return `No price configured for ${o.model ?? 'this model'}`;
  }
  return undefined;
}
