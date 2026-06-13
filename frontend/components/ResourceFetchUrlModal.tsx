import React, { useState } from 'react';
import { X, Loader2, Globe, Link2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { parseShareLink } from '../services/parserService';
import { useToast } from './Toast';

interface ResourceFetchUrlModalProps {
  isOpen: boolean;
  onClose: () => void;
}

/**
 * Bug F (issue #194) UI closure — replaces the previous "网页地址 即将推出"
 * placeholder with a real URL input that hits POST /api/v1/media/fetch.
 *
 * Backend has been ready for months (parserService.parseShareLink is
 * the one ChatGPT extension + ParserPage already use). The Resources
 * page just never had a UI entry point. This modal is the smallest
 * possible bridge: paste link → submit → toast with task_id → user
 * can monitor via Task Center.
 *
 * Intentional non-features:
 * - No tag picker here. The standalone /parser page has a richer
 *   form with tags / batch / scope selection. This modal stays
 *   single-shot to keep the resource-library "新建" flow snappy.
 * - No live progress polling. Task Center already does that, and
 *   inline polling here would block the modal close.
 */
export const ResourceFetchUrlModal: React.FC<ResourceFetchUrlModalProps> = ({
  isOpen,
  onClose,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [url, setUrl] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset state when the modal opens — otherwise a previous error /
  // url leaks into the next session if the user cancelled mid-edit.
  React.useEffect(() => {
    if (isOpen) {
      setUrl('');
      setError(null);
      setIsSubmitting(false);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;
    if (!/^https?:\/\//i.test(trimmed)) {
      setError(t('resources.fetchUrl.invalidScheme') || 'URL must start with http(s)://');
      return;
    }

    setIsSubmitting(true);
    setError(null);
    try {
      const result = await parseShareLink(trimmed);
      // parseShareLink returns FetchResponse — success indicates the
      // parse task was queued (or the resource was already owned and
      // returned via dedup). Either way the user sees the right thing
      // in their library / task center, no need to branch on async vs
      // already-owned here.
      const successMsg = result.message
        || t('resources.fetchUrl.successFallback')
        || 'Parse task submitted';
      addToast(successMsg, 'success');
      onClose();
    } catch (err: any) {
      setError(err?.message || t('resources.fetchUrl.failed') || 'Failed to submit');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={isSubmitting ? undefined : onClose}
      />

      <div className="relative bg-ink-900 border border-ink-800 rounded-2xl w-full max-w-md mx-4 shadow-2xl animate-in zoom-in-95 fade-in duration-200">
        <div className="flex items-center justify-between p-5 border-b border-ink-800">
          <h2 className="text-lg font-semibold text-ink-50 flex items-center gap-2">
            <Globe size={20} className="text-indigo-400" />
            {t('resources.fetchUrl.title') || 'Fetch from URL'}
          </h2>
          <button
            onClick={onClose}
            disabled={isSubmitting}
            className="p-2 rounded-lg text-ink-400 hover:text-ink-50 hover:bg-ink-800 transition-colors disabled:opacity-50"
          >
            <X size={20} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
              {error}
            </div>
          )}

          <div className="space-y-2">
            <label className="text-sm font-medium text-ink-400">
              {t('resources.fetchUrl.label') || 'Media link'}
            </label>
            <div className="relative">
              <Link2
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-500"
              />
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder={
                  t('resources.fetchUrl.placeholder')
                  || 'Paste a YouTube / Bilibili / Douyin / X link'
                }
                className="w-full bg-ink-800 border border-ink-700 rounded-lg pl-10 pr-4 py-2.5 text-ink-50 focus:border-indigo-500 outline-none transition-colors"
                autoFocus
                disabled={isSubmitting}
              />
            </div>
            <p className="text-xs text-ink-500">
              {t('resources.fetchUrl.hint')
                || 'The download will appear in Task Center, then in your library.'}
            </p>
          </div>

          <button
            type="submit"
            disabled={!url.trim() || isSubmitting}
            className="w-full py-3 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg font-medium transition-colors flex items-center justify-center gap-2"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="animate-spin" size={18} />
                {t('resources.fetchUrl.submitting') || 'Submitting...'}
              </>
            ) : (
              t('resources.fetchUrl.submit') || 'Parse'
            )}
          </button>
        </form>
      </div>
    </div>
  );
};
