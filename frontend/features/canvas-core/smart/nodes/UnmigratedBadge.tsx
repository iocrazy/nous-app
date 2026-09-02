/**
 * `Unmigrated` — the badge a legacy entity card wears when the asset library
 * has no asset carrying its provenance (P4 Task 6).
 *
 * It exists as its own component for one reason: BOTH legacy views must say it
 * the same way. `CharacterNodeView` and `LibEntityNodeView` are separate files
 * with separate head rows, and two copies of this markup would be two places
 * for the wording, the tone and the tooltip to drift.
 *
 * The tooltip matters as much as the badge. "Unmigrated" alone reads like an
 * error the user caused; the title says what actually happened and what it
 * costs them — the card still works as a note, it just no longer points at a
 * library row, so it feeds nothing into a generation.
 *
 * `warn`, not `danger`: nothing is broken and nothing was lost. The card is
 * exactly as it was; it simply cannot be turned into an asset reference
 * automatically.
 */

import type { TFunction } from 'i18next';

export function UnmigratedBadge({ t }: { t: TFunction }) {
  return (
    <span
      data-testid="legacy-unmigrated-badge"
      title={t(
        'canvas.legacyCard.unmigratedHint',
        'No asset in this workspace matches this card. Replace it with an asset card to use it in generations.',
      )}
      className="rounded-full bg-warn-soft px-2 py-0.5 text-[10px] font-medium text-warn"
    >
      {t('canvas.legacyCard.unmigrated', 'Unmigrated')}
    </span>
  );
}

export default UnmigratedBadge;
