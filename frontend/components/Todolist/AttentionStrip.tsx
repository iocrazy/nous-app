/**
 * A1 —— 「等我的」横条：Issues 页顶部一条，聚合三类卡在用户手上的事。
 *
 * Design rule that drives everything here: an EMPTY strip renders nothing at
 * all — not a "nothing waiting" placeholder. A band that is always present
 * teaches people to look past it, and its whole value is that showing up
 * means something is genuinely blocked on them.
 *
 * Card colours are the semantic tokens, one per source, so the three read as
 * different kinds of ask at a glance: question = warn, approval = info,
 * review = ok, paused = info.
 */

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronRight, HelpCircle, PauseCircle, ShieldCheck, CheckCircle2, Layers } from 'lucide-react';

import type { AttentionItem, AttentionType } from './attentionItems';

const STORAGE_KEY_PREFIX = 'mediahub:todolist:attention';

/** Collapsed state per scope. Defaults to expanded — see the module note. */
export function loadAttentionCollapsed(scopeKey: string): boolean {
  try {
    return window.localStorage.getItem(`${STORAGE_KEY_PREFIX}:${scopeKey}`) === '1';
  } catch (err) {
    console.error('[AttentionStrip] collapse state read failed', err);
    return false;
  }
}

export function saveAttentionCollapsed(scopeKey: string, collapsed: boolean): void {
  try {
    window.localStorage.setItem(`${STORAGE_KEY_PREFIX}:${scopeKey}`, collapsed ? '1' : '0');
  } catch (err) {
    console.error('[AttentionStrip] collapse state write failed', err);
  }
}

const CARD_STYLE: Record<AttentionType, string> = {
  question: 'border-warn-line bg-warn-soft',
  approval: 'border-info-line bg-info-soft',
  review: 'border-ok-line bg-ok-soft',
  paused: 'border-info-line bg-info-soft',
  queued: 'border-info-line bg-info-soft',
};

const CARD_ICON: Record<AttentionType, React.ComponentType<{ size?: number; className?: string }>> = {
  question: HelpCircle,
  approval: ShieldCheck,
  review: CheckCircle2,
  paused: PauseCircle,
  queued: Layers,
};

const ICON_TINT: Record<AttentionType, string> = {
  question: 'text-warn',
  approval: 'text-info',
  review: 'text-ok',
  paused: 'text-info',
  queued: 'text-info',
};

interface AttentionStripProps {
  items: AttentionItem[];
  collapsed: boolean;
  onToggle: (collapsed: boolean) => void;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  teamId?: string;
  /** Resolves an issue id to a detail route, or null when it isn't in the
   *  current list (the needs-input feed carries no identifier of its own). */
  issueLinkFor: (issueId: number) => string | null;
}

const AttentionCard: React.FC<{
  item: AttentionItem;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  issueLinkFor: (issueId: number) => string | null;
}> = ({ item, onApprove, onReject, issueLinkFor }) => {
  const { t } = useTranslation();
  const Icon = CARD_ICON[item.type];
  const href = item.issueId != null ? issueLinkFor(item.issueId) : null;

  const body = (
    <>
      <div className="flex items-center gap-1.5">
        <Icon size={13} className={`${ICON_TINT[item.type]} shrink-0`} />
        <span className="text-[13px] font-medium text-ink-200 truncate">{item.title}</span>
      </div>
      {item.detail && (
        <p className="mt-1 text-[12px] text-ink-400 line-clamp-2 break-words">{item.detail}</p>
      )}
      {item.type === 'approval' && (
        <div className="mt-2 flex items-center gap-2">
          <button
            type="button"
            onClick={() => onApprove(item.id)}
            className="px-2 py-0.5 text-[12px] rounded border border-ok-line text-ok hover:bg-ok-soft"
          >
            {t('issues.approve', 'Approve')}
          </button>
          <button
            type="button"
            onClick={() => onReject(item.id)}
            className="px-2 py-0.5 text-[12px] rounded border border-line text-ink-400 hover:bg-ink-800/60"
          >
            {t('issues.reject', 'Reject')}
          </button>
        </div>
      )}
    </>
  );

  const className = `w-56 shrink-0 rounded-lg border px-2.5 py-2 ${CARD_STYLE[item.type]}`;

  // Approval cards never link out — they are acted on in place.
  return href && item.type !== 'approval'
    ? <Link data-testid="attention-card" to={href} className={`${className} block hover:brightness-110`}>{body}</Link>
    : <div data-testid="attention-card" className={className}>{body}</div>;
};

export const AttentionStrip: React.FC<AttentionStripProps> = ({
  items, collapsed, onToggle, onApprove, onReject, issueLinkFor,
}) => {
  const { t } = useTranslation();
  if (items.length === 0) return null;

  return (
    <section
      data-testid="attention-strip"
      className="mx-4 mt-2 rounded-lg border border-warn-line bg-warn-soft/40 px-3 py-2"
    >
      <button
        type="button"
        data-testid="attention-toggle"
        onClick={() => onToggle(!collapsed)}
        aria-expanded={!collapsed}
        className="flex items-center gap-1.5 text-[12px] text-ink-300 hover:text-ink-100"
      >
        {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
        <span className="uppercase tracking-wider">{t('issues.waitingOnYou', 'Waiting on you')}</span>
        <span className="px-1.5 py-0.5 rounded-full bg-warn-soft text-warn text-[11px] tabular-nums">
          {items.length}
        </span>
      </button>
      {!collapsed && (
        <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
          {items.map((item) => (
            <AttentionCard
              key={item.id}
              item={item}
              onApprove={onApprove}
              onReject={onReject}
              issueLinkFor={issueLinkFor}
            />
          ))}
        </div>
      )}
    </section>
  );
};

export default AttentionStrip;
