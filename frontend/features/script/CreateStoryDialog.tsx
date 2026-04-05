import { useState, useCallback } from 'react';
import { X, Sparkles, ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { generateOutline } from '../../services/scriptService';

interface OutlineResult {
  name: string;
  chapters: Array<{ title: string; summary: string }>;
}

const GENRES = [
  { value: '', label: 'Not specified' },
  { value: '悬疑', label: 'Suspense' },
  { value: '爱情', label: 'Romance' },
  { value: '科幻', label: 'Sci-Fi' },
  { value: '奇幻', label: 'Fantasy' },
  { value: '历史', label: 'Historical' },
  { value: '现代都市', label: 'Modern Urban' },
  { value: '喜剧', label: 'Comedy' },
  { value: '悲剧', label: 'Tragedy' },
  { value: '动作冒险', label: 'Action Adventure' },
  { value: '恐怖', label: 'Horror' },
];

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

export function CreateStoryDialog({ isOpen, onClose }: Props) {
  const { scriptId } = useParams<{ scriptId: string }>();

  const [premise, setPremise] = useState('');
  const [genre, setGenre] = useState('');
  const [chapterCount, setChapterCount] = useState(5);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [outlineResult, setOutlineResult] = useState<OutlineResult | null>(null);
  const [storyName, setStoryName] = useState('');
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleGenerate = useCallback(async () => {
    if (!premise.trim() || !scriptId) return;
    setGenerating(true);
    setError(null);

    try {
      const result = await generateOutline({
        script_id: scriptId,
        premise: premise.trim(),
        chapter_count: chapterCount,
        genre: genre || undefined,
      });

      if (result.outline) {
        setOutlineResult(result.outline);
        setStoryName(result.outline.name ?? '');
      } else {
        onClose();
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      console.error('[CreateStoryDialog] Failed:', message);
    } finally {
      setGenerating(false);
    }
  }, [premise, chapterCount, genre, scriptId, onClose]);

  const handleRegenerate = useCallback(async () => {
    setOutlineResult(null);
    setStoryName('');
    await handleGenerate();
  }, [handleGenerate]);

  const handleConfirm = useCallback(() => {
    onClose();
  }, [onClose]);

  const handleClose = useCallback(() => {
    setOutlineResult(null);
    setStoryName('');
    setError(null);
    onClose();
  }, [onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[520px] max-h-[90vh] flex flex-col bg-zinc-900 rounded-xl border border-zinc-700 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-800 flex-shrink-0">
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-violet-400" />
            <h3 className="text-sm font-semibold text-white">Generate Story Outline</h3>
          </div>
          <button onClick={handleClose} className="text-zinc-500 hover:text-zinc-300">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-4 overflow-y-auto flex-1">
          {/* Premise input — hidden once outline is generated */}
          {!outlineResult && (
            <>
              <div>
                <label className="block text-xs font-medium text-zinc-400 mb-1.5">Story Premise</label>
                <textarea
                  className="w-full bg-zinc-800 text-sm text-zinc-200 rounded-lg px-3 py-2 resize-none outline-none focus:ring-1 focus:ring-violet-500/50 min-h-[100px]"
                  placeholder="Describe your story idea, setting, main characters, and conflict..."
                  value={premise}
                  onChange={(e) => setPremise(e.target.value)}
                  maxLength={10000}
                />
                <p className="text-[11px] text-zinc-600 mt-1">{premise.length} / 10,000</p>
              </div>

              {/* Advanced Settings */}
              <div>
                <button
                  type="button"
                  onClick={() => setShowAdvanced((v) => !v)}
                  className="flex items-center gap-1.5 text-xs text-zinc-400 hover:text-zinc-200 transition-colors"
                >
                  {showAdvanced ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                  Advanced Settings
                </button>

                {showAdvanced && (
                  <div className="mt-3 space-y-4 pl-1">
                    {/* Genre dropdown */}
                    <div>
                      <label className="block text-xs font-medium text-zinc-400 mb-1.5">Genre</label>
                      <select
                        value={genre}
                        onChange={(e) => setGenre(e.target.value)}
                        className="w-full bg-zinc-800 text-sm text-zinc-200 rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-violet-500/50 border border-zinc-700 appearance-none cursor-pointer"
                      >
                        {GENRES.map((g) => (
                          <option key={g.value} value={g.value}>
                            {g.label}
                          </option>
                        ))}
                      </select>
                    </div>

                    {/* Chapter count slider */}
                    <div>
                      <label className="block text-xs font-medium text-zinc-400 mb-1.5">
                        Number of Chapters
                      </label>
                      <div className="flex items-center gap-3">
                        <input
                          type="range"
                          min={1}
                          max={20}
                          value={chapterCount}
                          onChange={(e) => setChapterCount(Number(e.target.value))}
                          className="flex-1 accent-violet-500"
                        />
                        <span className="text-sm font-mono text-red-400 w-6 text-center font-bold">
                          {chapterCount}
                        </span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </>
          )}

          {/* Outline Result View */}
          {outlineResult && (
            <div className="space-y-4">
              {/* Original premise recap */}
              <div>
                <p className="text-[11px] text-zinc-600 mb-1">Story Concept</p>
                <p className="text-xs text-zinc-500 leading-relaxed">{premise}</p>
              </div>

              {/* Story name + Regenerate */}
              <div>
                <label className="block text-xs font-medium text-zinc-400 mb-1.5">Story Name</label>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={storyName}
                    onChange={(e) => setStoryName(e.target.value)}
                    className="flex-1 bg-zinc-800 text-sm text-zinc-200 rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-violet-500/50"
                    placeholder="Story title..."
                  />
                  <button
                    onClick={handleRegenerate}
                    disabled={generating}
                    title="Regenerate outline"
                    className="flex items-center gap-1 px-2.5 py-2 text-xs text-zinc-400 hover:text-zinc-200 bg-zinc-800 hover:bg-zinc-700 rounded-lg transition-colors disabled:opacity-50"
                  >
                    <RefreshCw size={13} className={generating ? 'animate-spin' : ''} />
                    Regenerate
                  </button>
                </div>
              </div>

              {/* Chapter list */}
              <div>
                <p className="text-xs font-medium text-zinc-400 mb-2">
                  Chapters ({outlineResult.chapters.length})
                </p>
                <div className="space-y-2 max-h-[280px] overflow-y-auto pr-1">
                  {outlineResult.chapters.map((ch, idx) => (
                    <div
                      key={idx}
                      className="bg-zinc-800 rounded-lg px-3 py-2.5 border border-zinc-700/50"
                    >
                      <p className="text-xs font-medium text-zinc-200">
                        <span className="text-zinc-500 mr-1.5">#{idx + 1}</span>
                        {ch.title}
                      </p>
                      {ch.summary && (
                        <p className="text-[11px] text-zinc-500 mt-1 leading-relaxed">{ch.summary}</p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {error && (
            <p className="text-xs text-red-400 bg-red-900/20 rounded-lg px-3 py-2">{error}</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-zinc-800 flex-shrink-0">
          <button
            onClick={handleClose}
            className="px-3 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 rounded-lg hover:bg-zinc-800 transition-colors"
          >
            Cancel
          </button>

          {outlineResult ? (
            <button
              onClick={handleConfirm}
              className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-500 rounded-lg transition-colors"
            >
              Confirm Create
            </button>
          ) : (
            <button
              onClick={handleGenerate}
              disabled={!premise.trim() || generating}
              className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white bg-violet-600 hover:bg-violet-500 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Sparkles size={12} />
              {generating ? 'Generating...' : 'Generate Outline'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
