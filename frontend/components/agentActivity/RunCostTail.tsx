/**
 * 一次回合「共消耗」多少（3c §4.2）。`chargedPoints` 是积分账里**真的扣掉的**积分，
 * `costCents` 是效率账算出来的花费。有前者就显示前者——读者问的是「我被扣了多少」。
 * BYOK / 急停 / 零花费没有积分流水，才退回 ¢。
 *
 * ⚠️ `chargedPoints` 是**以这条 run 为根的整棵树**的合计，不是 root 那一条流水（3c
 * 终审 I2 修正）：扣费逐 run 发生（每条各 ceil 一次自身花费），而读者问的是这次回合。
 * 浮层里那句 `incl. sub-agents` 就是在说这件事——数字比 root 那一行大不是 bug。
 *
 * `costCents` 为 `0` 就渲染 `¢0.00`，不是 `—`：`/ai-library/runs/costs` 的 `cost_cents`
 * 自 mig 479 起来自 `Σ own_cost_cents`（每次镜像都写、存量已回填），0 就是零花费的
 * 事实，不是「没算出价」。`null` 只可能来自其他来源（done 帧 / rollup 行）在字段
 * 缺席时——那才是真缺席，读作 `—`。
 */
import React from 'react';
import { useTranslation } from 'react-i18next';

export interface RunCostTailProps {
  costCents: number | null;
  chargedPoints: number | null;
  model: string | null;
  /** run 行自己记的收尾状态；`null` = 这行从来没记过（见 `RunCost.status`）。 */
  status: string | null;
  promptTokens?: number;
  completionTokens?: number;
  /** 回合还没结束——值会变，加 `…` 说明它不是终值。 */
  live?: boolean;
}

export const RunCostTail: React.FC<RunCostTailProps> = ({
  costCents,
  chargedPoints,
  model,
  status,
  promptTokens,
  completionTokens,
  live = false,
}) => {
  const { t } = useTranslation();
  const amount =
    chargedPoints !== null
      ? `◇ ${chargedPoints.toFixed(2)}`
      : costCents !== null
        ? `¢${costCents.toFixed(2)}` // 含 0——零花费不是缺席
        : '—'; // 真缺席（done 帧 / rollup 行没这一列）才落到这里
  // `—` 不加 `…`：一个不存在的数字「还会变」是句废话。
  const head = live && amount !== '—' ? `${amount}…` : amount;
  const title = [
    promptTokens !== undefined || completionTokens !== undefined
      ? t('cost.tailTokens', '{{p}} prompt · {{c}} completion tokens', {
          p: promptTokens ?? 0,
          c: completionTokens ?? 0,
        })
      : null,
    costCents !== null ? `¢${costCents.toFixed(2)}` : null,
    chargedPoints !== null
      ? // `incl. sub-agents` 不是废话：这个数是整棵树的合计，比 root 那一条流水大。
        t('cost.charged', 'Charged ◇ {{n}} (incl. sub-agents)', {
          n: chargedPoints.toFixed(2),
        })
      : // 说清为什么没扣。连 status 都没有时说 `Not charged` 就停住——括号里
        // 塞一个 `null` 或 `unknown` 是在假装知道原因。
        status
        ? t('cost.notCharged', 'Not charged ({{why}})', { why: status })
        : t('cost.notChargedPlain', 'Not charged'),
  ]
    .filter(Boolean)
    .join('\n');
  return (
    <span
      className="text-[11px] text-ink-500 tabular-nums"
      data-testid="run-cost-tail"
      title={title}
    >
      {[head, model].filter(Boolean).join(' · ')}
    </span>
  );
};

export default RunCostTail;
