/**
 * ChapterFallback — a read-only prose card for a legacy chapter that has no
 * scenes yet (spec §6 P1 明项, Task 10).
 *
 * The v2 editor writes element-structured scenes, but earlier drafts stored a
 * chapter's body as a single plain-text column. Rather than silently hide that
 * content, any chapter with no scene pointing at it renders here after the scene
 * list: its title, its plain-text body, and a "Convert to Scenes" action that
 * dispatches the backend convert workflow. While the conversion runs the button
 * shows a converting state; the shell polls listScenes until the new scenes
 * materialise (see EditorShell).
 */
import { useTranslation } from 'react-i18next';
import type { ScriptChapter } from '../../types';

export interface ChapterFallbackProps {
  chapter: ScriptChapter;
  converting: boolean;
  /** AI-split the chapter's prose into scenes (only meaningful when it HAS prose). */
  onConvert: (chapterId: string) => void;
  /** Turn an EMPTY chapter straight into a plain, typeable scene (no LLM). */
  onStartWriting: (chapterId: string) => void;
}

export function ChapterFallback({
  chapter,
  converting,
  onConvert,
  onStartWriting,
}: ChapterFallbackProps) {
  const { t } = useTranslation();
  const title = chapter.title?.trim() || t('editor.untitledChapter');
  const body = chapter.content?.trim() ?? '';
  // An empty chapter has nothing for the AI to split; offer a direct "start
  // writing" that materialises a typeable scene, not a convert that spins.
  const isEmpty = body.length === 0;

  return (
    <div className="mh-chapter-fallback" data-testid="chapter-fallback" data-chapter-id={chapter.id}>
      <div className="mh-chapter-fallback-head">
        <h3 className="mh-chapter-fallback-title">{title}</h3>
        <button
          type="button"
          className="mh-chapter-convert-btn"
          disabled={converting}
          aria-busy={converting}
          onClick={() => (isEmpty ? onStartWriting(chapter.id) : onConvert(chapter.id))}
        >
          {converting
            ? t('editor.converting')
            : isEmpty
              ? t('editor.startWriting')
              : t('editor.convertToScenes')}
        </button>
      </div>
      {body ? (
        <p className="mh-chapter-fallback-body">{body}</p>
      ) : (
        <p className="mh-chapter-fallback-body empty">{t('editor.chapterEmpty')}</p>
      )}
    </div>
  );
}
