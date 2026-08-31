// frontend/components/resources/assets/sheet/AudioSheetBody.tsx
//
// Screen 4c: an audio asset shows a waveform where the other types show a
// board, plus its Variants list.
//
// `AudioWaveformPlayer` is the app's existing player, reused as-is - it takes
// a `src` and a filename and owns everything else. Its import chain is three
// modules deep (lucide + `utils/sodaTheme`), so pulling it in here costs
// nothing a sheet was not already paying.
//
// When the primary slot is EMPTY there is no player and no silent gap: the
// same dashed `Equip / Generate` affordance the board uses appears instead,
// because "this audio asset has no audio yet" is a fact the page must state.
//
// `loopable` / duration are NOT here - they are header extras, next to the
// name, alongside the location sheet's interior/exterior chip.

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AudioLines, Sparkles, Wand2 } from 'lucide-react';

import { AudioWaveformPlayer } from '../../../AudioWaveformPlayer';
import type { AssetRowDetail } from '../../../../services/assetsService';
import { getResourceFileUrl } from '../../../../services/resourceService';
import { slotLabelKey } from '../assetTypeMeta';
import { audioDurationSec, filesForSlot } from './assetSheetModel';

export interface AudioSheetBodyProps {
  detail: AssetRowDetail;
  readOnly: boolean;
  /** Task 8 hook points, same contract as the board's. */
  onEquip?: (slot: string) => void;
  onGenerate?: (slot: string) => void;
}

export const AudioSheetBody: React.FC<AudioSheetBodyProps> = ({
  detail,
  readOnly,
  onEquip,
  onGenerate,
}) => {
  const { t } = useTranslation();
  const primary = filesForSlot(detail.files, 'primary', null)[0] ?? null;
  const variants = filesForSlot(detail.files, 'variants', null);
  const duration = audioDurationSec(detail.attrs);
  const [playing, setPlaying] = useState<string | null>(null);
  const soon = t('assets.sheet.comingSoon', 'Arrives shortly');

  return (
    <section data-testid="audio-body" className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <h3 className="text-[13px] font-medium text-content-2">
          {t(slotLabelKey('primary'), 'Primary')}
        </h3>
        {primary ? (
          <AudioWaveformPlayer
            src={getResourceFileUrl(primary.resource_id)}
            filename={detail.name}
            duration={duration ?? undefined}
          />
        ) : (
          <div
            data-testid="audio-empty"
            className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line-strong px-4 py-8"
          >
            <AudioLines size={18} className="text-content-4" aria-hidden="true" />
            <p className="text-[12px] text-content-3">
              {t('assets.sheet.noAudio', 'No Audio Attached Yet')}
            </p>
            {!readOnly && (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  data-testid="pin-equip"
                  data-slot="primary"
                  disabled={!onEquip}
                  title={onEquip ? undefined : soon}
                  onClick={() => onEquip?.('primary')}
                  className="inline-flex items-center gap-1 rounded border border-line-strong px-2 py-0.5 text-[11px] font-medium text-content-2 hover:bg-island-2 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Wand2 size={11} aria-hidden="true" />
                  {t('assets.sheet.equip', 'Equip')}
                </button>
                <button
                  type="button"
                  data-testid="pin-generate"
                  data-slot="primary"
                  disabled={!onGenerate}
                  title={onGenerate ? undefined : soon}
                  onClick={() => onGenerate?.('primary')}
                  className="inline-flex items-center gap-1 rounded border border-line-strong px-2 py-0.5 text-[11px] font-medium text-content-2 hover:bg-island-2 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Sparkles size={11} aria-hidden="true" />
                  {t('assets.sheet.generate', 'Generate')}
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-2" data-testid="audio-variants">
        <div className="flex items-center gap-2">
          <h3 className="text-[13px] font-medium text-content-2">
            {t(slotLabelKey('variants'), 'Variants')}
          </h3>
          <span className="text-[11px] tabular-nums text-content-4">{variants.length}</span>
        </div>
        {variants.length === 0 ? (
          <p className="text-[12px] text-content-4">
            {t('assets.sheet.noVariants', 'No Variants Yet')}
          </p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {variants.map((file) => (
              <li
                key={file.resource_id}
                data-testid="audio-variant"
                data-resource-id={file.resource_id}
                className="flex flex-col gap-1.5 rounded-lg border border-line bg-card px-2.5 py-2"
              >
                <button
                  type="button"
                  onClick={() =>
                    setPlaying((current) =>
                      current === file.resource_id ? null : file.resource_id,
                    )
                  }
                  className="flex items-center gap-2 text-left text-[12px] text-content"
                >
                  <AudioLines size={12} className="text-content-4" aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate">
                    {file.note ?? file.resource_id}
                  </span>
                </button>
                {/* Mounted only when opened: a page of eight variants would
                    otherwise start eight media downloads on load. */}
                {playing === file.resource_id && (
                  <AudioWaveformPlayer
                    src={getResourceFileUrl(file.resource_id)}
                    filename={file.note ?? detail.name}
                  />
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

    </section>
  );
};
