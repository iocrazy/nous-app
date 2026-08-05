/**
 * The injected selection, as a closable capsule above the composer (design §D,
 * spec §5.3's "面板呈现" row).
 *
 * Previously a script selection was spliced into the composer as a blockquote:
 * the user's own message and the quoted material became one indistinguishable
 * blob of text they then had to edit around. As a capsule it stays legible
 * ("Selection from S2 · dialogue"), stays removable, and — the point of the
 * change — remains structurally identifiable right up to send.
 *
 * NOTE this is the presentation half only. Spec §5.3 also wants the payload
 * itself to become a `{scene_id, element_ids}` handle the agent can edit
 * precisely, rather than copied text. The scene/element ids ARE carried here
 * and shown, but the text is still what gets sent — turning that into a real
 * structured attachment is §5.3's own work, not A7's.
 */

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, Quote, X } from 'lucide-react';

export interface ContextCapsuleValue {
  text: string;
  sceneLabel?: string;
  sceneId?: string;
  elementId?: string;
  elementType?: string;
  crossScene?: boolean;
}

export interface ContextCapsuleProps {
  value: ContextCapsuleValue;
  onDismiss: () => void;
}

export function ContextCapsule({
  value,
  onDismiss,
}: ContextCapsuleProps): React.ReactElement {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  const scene = value.sceneLabel;
  const title = scene
    ? t('chat.selectionFrom', 'Selection from {{scene}}', { scene })
    : t('chat.selection', 'Selection');
  // The "signal": what KIND of material this is, so the capsule says more than
  // "some text". Cross-scene wins because it changes what the agent can do.
  const signal = value.crossScene
    ? t('agentActivity.crossScene', 'multiple scenes')
    : (value.elementType ?? null);

  return (
    <div
      data-testid="context-capsule"
      data-scene-id={value.sceneId ?? ''}
      className="mx-3 mb-1.5 rounded-lg border border-info-line bg-info-soft"
    >
      <div className="flex items-center gap-1.5 px-2 py-1.5">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          data-testid="context-capsule-toggle"
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left text-[11px] text-info"
        >
          {expanded ? (
            <ChevronDown size={11} className="shrink-0" />
          ) : (
            <ChevronRight size={11} className="shrink-0" />
          )}
          <Quote size={11} className="shrink-0" />
          <span className="truncate font-medium">{title}</span>
          {signal && (
            <span className="shrink-0 text-ink-500">· {signal}</span>
          )}
        </button>
        <button
          type="button"
          onClick={onDismiss}
          data-testid="context-capsule-dismiss"
          title={t('agentActivity.dropContext', 'Remove this selection')}
          className="shrink-0 rounded p-0.5 text-ink-500 transition-colors hover:bg-ink-800 hover:text-ink-300"
        >
          <X size={12} />
        </button>
      </div>
      {expanded && (
        <p
          data-testid="context-capsule-text"
          className="max-h-32 overflow-y-auto whitespace-pre-wrap break-words border-t border-info-line px-3 py-1.5 text-[11px] leading-relaxed text-ink-300"
        >
          {value.text}
        </p>
      )}
    </div>
  );
}
