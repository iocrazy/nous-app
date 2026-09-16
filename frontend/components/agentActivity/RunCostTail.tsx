/**
 * 一次回合「共消耗」多少（3c §4.2）。`chargedPoints` 是积分账里**真的扣掉的那一行**
 * （`point_transactions` 的 consume 行），`costCents` 是效率账算出来的花费。有前者就显示前者——
 * 读者问的是「我被扣了多少」。BYOK / 急停 / 零花费没有积分行，才退回 ¢。
 *
 * 两个都没有读作 `—`，不是 `¢0.00`：0 会把「没算出价」说成「这次免费」，而那是两个答案。
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
        ? `¢${costCents.toFixed(2)}`
        : '—';
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
      ? t('cost.charged', 'Charged ◇ {{n}}', { n: chargedPoints.toFixed(2) })
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
