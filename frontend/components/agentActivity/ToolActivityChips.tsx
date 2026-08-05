/**
 * The "读取剧本场景 ✓" chip row (spec §5.4).
 *
 * One chip per executed tool call, in emission order, so a turn where the
 * agent read three scenes and wrote four cards no longer looks identical to a
 * turn where it only talked. Purely presentational — the caller decides which
 * source the activities came from (see toolActivity.ts).
 */

import React from 'react';
import { useTranslation } from 'react-i18next';
import {
  BookOpen,
  Check,
  Image as ImageIcon,
  Lightbulb,
  PencilLine,
  Wrench,
  X,
} from 'lucide-react';

import { TOOL_LABEL_PREFIX, type ToolActivity, type ToolActivityKind } from './toolActivity';

/** Semantic tokens only (K1 §2.3) — no literal hue class names. */
const KIND_TONE: Record<ToolActivityKind, string> = {
  read: 'text-info border-info-line bg-info-soft',
  write: 'text-agent border-agent-line bg-agent-soft',
  propose: 'text-warn border-warn-line bg-warn-soft',
  generate: 'text-agent border-agent-line bg-agent-soft',
  other: 'text-info border-info-line bg-info-soft',
};

function KindIcon({ kind }: { kind: ToolActivityKind }): React.ReactElement {
  switch (kind) {
    case 'read':
      return <BookOpen size={11} className="shrink-0" />;
    case 'write':
      return <PencilLine size={11} className="shrink-0" />;
    case 'propose':
      return <Lightbulb size={11} className="shrink-0" />;
    case 'generate':
      return <ImageIcon size={11} className="shrink-0" />;
    default:
      return <Wrench size={11} className="shrink-0" />;
  }
}

export interface ToolActivityChipsProps {
  activities: ToolActivity[];
  className?: string;
}

export function ToolActivityChips({
  activities,
  className,
}: ToolActivityChipsProps): React.ReactElement | null {
  const { t } = useTranslation();
  if (!activities || activities.length === 0) return null;

  return (
    <div
      className={`flex flex-wrap gap-1.5 ${className ?? ''}`}
      data-testid="tool-activity-chips"
    >
      {activities.map((act) => {
        const label = t(`${TOOL_LABEL_PREFIX}${act.tool}`, act.tool);
        const tone = act.ok
          ? KIND_TONE[act.kind]
          : 'text-danger border-danger-line bg-danger-soft';
        // The failure reason is the one thing worth surfacing on hover — a
        // chip that just says "✗" leaves the user guessing.
        const title = act.ok
          ? label
          : `${label} — ${act.errorText ?? t('agentActivity.failed', 'failed')}`;
        return (
          <span
            key={act.key}
            title={title}
            data-testid="tool-activity-chip"
            data-tool={act.tool}
            data-ok={act.ok ? 'true' : 'false'}
            className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] leading-none ${tone}`}
          >
            <KindIcon kind={act.kind} />
            <span className="truncate max-w-[180px]">{label}</span>
            {act.ok ? (
              <Check size={11} className="shrink-0" aria-hidden />
            ) : (
              <X size={11} className="shrink-0" aria-hidden />
            )}
          </span>
        );
      })}
    </div>
  );
}
