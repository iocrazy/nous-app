// components/Distribution/CoverStudio/CoverReferencePool.tsx
//
// The "References — n / 9" card: one flat list of pictures handed to the model,
// shared between frames grabbed from the video and templates saved earlier.
//
// One list rather than two is a decision, not a layout accident. The model
// receives a single array; showing two budgets would let someone fill both and
// lose the overflow without being told. The card therefore states the shared
// count in its own header, and refusing an add says which of the two reasons
// it was.
//
// The rules live in coverReferences.ts so they can be tested without rendering.

import React, { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Upload, User, X } from 'lucide-react';

import { HelpTip } from './HelpTip';

import { getApiUrl } from '../../../utils/apiConfig';
import {
  MAX_REFERENCES,
  formatTimestamp,
  isFull,
  type CoverReference,
} from './coverReferences';
import './cover-studio.css';

interface Props {
  refs: CoverReference[];
  /** Whether the selected style demands a character reference. */
  requiresPerson?: boolean;
  onRemove: (genId: string) => void;
  /** Optional: when the template library sits right under this pool (the
   *  merged card), there is no "From templates" tile to jump to. */
  onAddFromTemplates?: () => void;
  /** Render without card chrome, as the top half of a merged card. */
  embedded?: boolean;
  /** Set when the last add was refused, so the card can say which reason. */
  refusal?: 'full' | 'duplicate' | null;
  /** A picture from disk straight into the pool (the design's "上传"). */
  onUpload?: (file: File) => void;
}

export function CoverReferencePool({
  refs,
  requiresPerson = false,
  onRemove,
  onAddFromTemplates,
  refusal = null,
  onUpload,
  embedded = false,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const uploadRef = useRef<HTMLInputElement>(null);
  const person = refs.find((r) => r.kind === 'person');
  const rest = refs.filter((r) => r.kind !== 'person');
  const full = isFull(refs);

  const caption = (ref: CoverReference): string => {
    if (ref.kind === 'frame') {
      return typeof ref.timestampSeconds === 'number'
        ? formatTimestamp(ref.timestampSeconds)
        : '';
    }
    return ref.label ?? '';
  };

  const badge = (kind: CoverReference['kind']): string => {
    if (kind === 'person') {
      return t('distribution.coverStudio.refPerson', 'Person');
    }
    if (kind === 'frame') {
      return t('distribution.coverStudio.refFrame', 'Frame');
    }
    return t('distribution.coverStudio.refTemplate', 'Template');
  };

  const Heading = embedded ? 'div' : 'h4';
  return (
    <div className={embedded ? 'cs-embedded' : 'cs-card'}>
      <Heading className={embedded ? 'cs-subhead' : undefined}>
        {t('distribution.coverStudio.references', 'Sent to the model')}
        {embedded && (
          <HelpTip
            text={t(
              'distribution.coverStudio.refExplainerMerged',
              'These nine slots are what the model receives. The library below is where you pick from — click a template to add it here.',
            )}
          />
        )}
        <span className={`aux ${full ? 'bad' : ''}`} data-testid="cover-ref-count">
          {/* The person rides in the same nine the server accepts, but it is
              not a slot the user fills here — so when the pool is drawn
              without the person tile, its budget is nine minus the person. */}
          {t('distribution.coverStudio.refCount', {
            defaultValue: '{{used}} / {{max}}',
            used: requiresPerson ? refs.length : rest.length,
            max: requiresPerson ? MAX_REFERENCES : MAX_REFERENCES - (person ? 1 : 0),
          })}
        </span>
      </Heading>

      <div className="cs-body">
        <div className="cs-pool" data-testid="cover-ref-pool">
          {/* The person slot renders even when empty, and only when the style
              asks for one. An always-present empty slot would read as a bug in
              styles that never use it; hiding it in a style that DOES need it
              would hide a blocker. */}
          {requiresPerson && (
            <div className={`rf ${person ? '' : 'missing'}`} data-testid="cover-ref-person">
              <span className="src lock">{badge('person')}</span>
              {person ? (
                <img src={`${getApiUrl()}${person.url}`} alt={badge('person')} />
              ) : (
                <span className="t">
                  <User size={13} />
                  <br />
                  {t('distribution.coverStudio.refPersonMissing', 'not set')}
                </span>
              )}
              {/* No remove button: the slot is reserved by the style. Grabbing
                  another frame of yourself replaces it — see addReference. */}
            </div>
          )}

          {rest.map((ref) => (
            <div key={ref.genId} className="rf">
              <span className="src">{badge(ref.kind)}</span>
              <img src={`${getApiUrl()}${ref.url}`} alt={caption(ref) || badge(ref.kind)} />
              <button
                type="button"
                className="x"
                aria-label={t('distribution.coverStudio.refRemove', 'Remove reference')}
                onClick={() => onRemove(ref.genId)}
              >
                <X size={10} />
              </button>
              <span className="t">{caption(ref)}</span>
            </div>
          ))}

          {onAddFromTemplates && (
            <button
              type="button"
              className="rf add"
              disabled={full}
              onClick={onAddFromTemplates}
              data-testid="cover-ref-add-template"
            >
              <Plus size={14} />
              {t('distribution.coverStudio.refFromTemplates', 'From templates')}
            </button>
          )}
          {onUpload && (
            <>
              <button
                type="button"
                className="rf add"
                disabled={full}
                onClick={() => uploadRef.current?.click()}
                data-testid="cover-ref-upload"
              >
                <Upload size={14} />
                {t('distribution.coverStudio.refUpload', 'Upload')}
              </button>
              <input
                ref={uploadRef}
                type="file"
                accept="image/*"
                hidden
                data-testid="cover-ref-upload-input"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) onUpload(f);
                  e.target.value = '';
                }}
              />
            </>
          )}
        </div>

        {/* A refused click has to say which refusal it was. "Nothing happened"
            after pressing Add is the failure mode this repo keeps re-learning. */}
        {refusal === 'full' && (
          <div className="cs-error" style={{ margin: '10px 0 0' }} data-testid="cover-ref-full">
            {t('distribution.coverStudio.refFullMsg', {
              defaultValue:
                'The pool already holds {{max}} pictures. Remove one to add another.',
              max: MAX_REFERENCES,
            })}
          </div>
        )}
        {refusal === 'duplicate' && (
          <div className="cs-error" style={{ margin: '10px 0 0' }} data-testid="cover-ref-dup">
            {t(
              'distribution.coverStudio.refDuplicateMsg',
              'That picture is already in the pool.',
            )}
          </div>
        )}

        {!embedded && (
          <p className="cs-hint" style={{ marginTop: 9 }}>
            {t(
              'distribution.coverStudio.refExplainer',
              'One list of nine, shared. Frames say what is in your video; templates say what it should look like.',
            )}
          </p>
        )}
      </div>
    </div>
  );
}
