/**
 * OutlineView — the Outline↔Script linkage surface (spec §5-2, Phase B Task 4).
 *
 * A same-source, read-only document tree: each chapter is a heading-weight title
 * row (H1 typographic level) with its scenes indented beneath as lighter H2/body
 * rows (number + INT/EXT · location · time + a first-action summary). Chapterless
 * scenes collect in an "Unassigned" group at the bottom. This view does NOT edit
 * prose (the top Outline toolbar stays disabled — that lands in a later phase);
 * it only NAVIGATES and REORDERS:
 *  - Clicking a scene row jumps back to the Script sheet on that scene (the
 *    shell's onOpenScene path, which also switches the top tab to Script).
 *  - Dragging a scene row within its group reorders it via moveScene with a
 *    before/after anchor; dragging across groups reparents it via moveScene with
 *    the target group's chapter_id (HTML5 DnD, the same drop-indicator pattern
 *    as SceneBlock's reorder).
 *
 * #1006: chapter ids are NATIVE NUMBERS at runtime even though typed string, so
 * every group-membership comparison coerces both sides with String().
 */
import { useCallback, useMemo, useRef, useState, type DragEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { moveScene } from '../sceneService';
import { sceneSummary } from '../nodes/sceneNodeMapper';
import type { SceneDoc } from '../types';
import type { ScriptChapter } from '../../types';
import { ChapterFallback } from './ChapterFallback';

/** Longest chapter-excerpt line rendered under a title before truncation. */
const EXCERPT_MAX = 80;

export interface OutlineViewProps {
  scenes: SceneDoc[];
  chapters: ScriptChapter[];
  /** Jump back to the Script sheet on this scene (shell switches tab + scrolls). */
  onOpenScene: (sceneId: string) => void;
  /** Re-fetch scenes+chapters after a reorder/reparent settles. */
  onReload: () => void | Promise<void>;
  /**
   * Legacy chapters with no scene pointing at them yet (A3: moved here from the
   * Script tab — they are not script content, just an unstarted prose stub with
   * a "Start Writing" / "Convert to Scenes" entry point).
   */
  orphanChapters?: ScriptChapter[];
  /** Per-chapter in-flight state for the convert/start-writing button. */
  converting?: Record<string, boolean>;
  /** AI-split a chapter's prose into scenes. */
  onConvert?: (chapterId: string) => void;
  /** Turn an EMPTY chapter straight into a plain, typeable scene (no LLM). */
  onStartWriting?: (chapterId: string) => void;
}

/** A group key that also identifies the reparent target chapter (null = Unassigned). */
interface OutlineGroup {
  key: string;
  chapterId: string | null;
  title: string;
  excerpt: string;
  scenes: SceneDoc[];
}

/** Same coercion the mapper uses: compare chapter keys as strings, null → ''. */
function chapterKey(id: unknown): string {
  return id == null ? '' : String(id);
}

/** `INT · Location · TIME`, empty parts dropped (mirrors SceneFlowNode's head). */
function headingLine(scene: SceneDoc): string {
  return [scene.heading_int_ext, scene.location_text, scene.time_of_day]
    .map((part) => (part ?? '').trim())
    .filter((part) => part.length > 0)
    .join(' · ');
}

/** First non-empty line of a chapter's summary/content, truncated for the excerpt. */
function chapterExcerpt(ch: ScriptChapter): string {
  const raw = (ch.summary || ch.content || '').trim();
  if (!raw) return '';
  const line = raw.split(/\r?\n/)[0].trim();
  return line.length > EXCERPT_MAX ? line.slice(0, EXCERPT_MAX) + '…' : line;
}

/** Which half of a row the pointer is over → the drop edge (same as SceneBlock). */
function edgeFromPointer(el: HTMLElement, clientY: number): 'before' | 'after' {
  const rect = el.getBoundingClientRect();
  return clientY < rect.top + rect.height / 2 ? 'before' : 'after';
}

export function OutlineView({
  scenes,
  chapters,
  onOpenScene,
  onReload,
  orphanChapters = [],
  converting = {},
  onConvert,
  onStartWriting,
}: OutlineViewProps) {
  const { t } = useTranslation();

  const [dragging, setDragging] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<{ sceneId: string; edge: 'before' | 'after' } | null>(
    null,
  );
  const [dropGroupKey, setDropGroupKey] = useState<string | null>(null);
  // Read the active drag id from a ref so the drop handler never sees a stale
  // closure between the separate dragstart/dragover/drop event ticks.
  const draggingRef = useRef<string | null>(null);

  // Orphan chapters (no scene yet) get their own "unstarted" card below with the
  // Start Writing / Convert action — they are excluded from the regular chapter
  // groups here so a same chapter never renders twice (once as an empty group,
  // once as the fallback card).
  const orphanIds = useMemo(
    () => new Set(orphanChapters.map((ch) => chapterKey(ch.id))),
    [orphanChapters],
  );

  const groups = useMemo<OutlineGroup[]>(() => {
    const claimed = new Set<string>();
    const chapterGroups: OutlineGroup[] = chapters
      .filter((ch) => !orphanIds.has(chapterKey(ch.id)))
      .map((ch) => {
        const key = chapterKey(ch.id);
        const groupScenes = scenes
          .filter((s) => s.chapter_id != null && chapterKey(s.chapter_id) === key)
          .sort((a, b) => a.sort_order - b.sort_order);
        groupScenes.forEach((s) => claimed.add(s.id));
        return {
          key: `ch-${key}`,
          chapterId: ch.id,
          title: ch.title?.trim() || t('editor.untitledChapter'),
          excerpt: chapterExcerpt(ch),
          scenes: groupScenes,
        };
      });
    // Everything a listed chapter did not claim (chapter_id null OR pointing at
    // an absent chapter) lands in Unassigned. Shown when it has scenes, or when
    // there are no chapters at all (so a flat script still renders its scenes).
    const unassigned = scenes
      .filter((s) => !claimed.has(s.id))
      .sort((a, b) => a.sort_order - b.sort_order);
    const all = [...chapterGroups];
    if (unassigned.length > 0 || chapters.length === 0) {
      all.push({
        key: 'unassigned',
        chapterId: null,
        title: t('editor.outlineUnassigned'),
        excerpt: '',
        scenes: unassigned,
      });
    }
    return all;
  }, [scenes, chapters, t, orphanIds]);

  // Continuous 1..N numbering across the whole outline in display order — the
  // rows read like a numbered document rather than restarting per chapter.
  const numberOf = useMemo(() => {
    const m = new Map<string, number>();
    let n = 0;
    for (const g of groups) for (const s of g.scenes) m.set(s.id, ++n);
    return m;
  }, [groups]);

  const beginDrag = useCallback((sceneId: string) => {
    draggingRef.current = sceneId;
    setDragging(sceneId);
  }, []);

  const endDrag = useCallback(() => {
    draggingRef.current = null;
    setDragging(null);
    setDropTarget(null);
    setDropGroupKey(null);
  }, []);

  const runMove = useCallback(
    async (
      sceneId: string,
      args: { chapter_id?: string | null; before_scene_id?: string; after_scene_id?: string },
    ) => {
      try {
        await moveScene(sceneId, args);
        await onReload();
      } catch (err) {
        console.error('[OutlineView] moveScene failed', err);
      }
    },
    [onReload],
  );

  const handleDropOnScene = useCallback(
    (target: SceneDoc, edge: 'before' | 'after') => {
      const draggedId = draggingRef.current;
      endDrag();
      if (!draggedId || draggedId === target.id) return;
      const dragged = scenes.find((s) => s.id === draggedId);
      if (!dragged) return;
      const anchor =
        edge === 'before' ? { before_scene_id: target.id } : { after_scene_id: target.id };
      const sameGroup = chapterKey(dragged.chapter_id) === chapterKey(target.chapter_id);
      void runMove(
        draggedId,
        sameGroup ? anchor : { chapter_id: target.chapter_id, ...anchor },
      );
    },
    [scenes, runMove, endDrag],
  );

  const handleDropOnGroup = useCallback(
    (group: OutlineGroup) => {
      const draggedId = draggingRef.current;
      endDrag();
      if (!draggedId) return;
      const dragged = scenes.find((s) => s.id === draggedId);
      if (!dragged) return;
      // Already in this group → nothing to reparent.
      if (chapterKey(dragged.chapter_id) === chapterKey(group.chapterId)) return;
      void runMove(draggedId, { chapter_id: group.chapterId });
    },
    [scenes, runMove, endDrag],
  );

  const handleRowKeyDown = useCallback(
    (sceneId: string, e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        onOpenScene(sceneId);
      }
    },
    [onOpenScene],
  );

  return (
    <div className="mh-outline" data-testid="outline-view" aria-label={t('editor.outlineLabel')}>
      {groups.map((group) => (
        <section
          key={group.key}
          className={`mh-outline-group${dropGroupKey === group.key ? ' drop-active' : ''}`}
        >
          <header
            className="mh-outline-chapter"
            onDragOver={
              dragging
                ? (e) => {
                    e.preventDefault();
                    setDropGroupKey(group.key);
                    setDropTarget(null);
                  }
                : undefined
            }
            onDrop={dragging ? () => handleDropOnGroup(group) : undefined}
          >
            <h3 className="mh-outline-chapter-title">{group.title}</h3>
            {group.excerpt && <p className="mh-outline-chapter-excerpt">{group.excerpt}</p>}
          </header>

          {group.scenes.length === 0 ? (
            <div className="mh-outline-empty">{t('editor.outlineEmptyGroup')}</div>
          ) : (
            <ol className="mh-outline-scenes">
              {group.scenes.map((scene) => {
                const heading = headingLine(scene);
                const summary = sceneSummary(scene);
                const isDragging = dragging === scene.id;
                const edge =
                  dropTarget && dropTarget.sceneId === scene.id ? dropTarget.edge : null;
                return (
                  <li key={scene.id} className="mh-outline-scene-wrap">
                    {edge === 'before' && (
                      <div className="mh-drop-indicator before" aria-hidden="true" />
                    )}
                    <div
                      className={`mh-outline-scene-row${isDragging ? ' dragging' : ''}`}
                      data-testid="outline-scene-row"
                      data-scene-id={scene.id}
                      role="button"
                      tabIndex={0}
                      aria-label={t('editor.outlineOpenScene')}
                      draggable
                      onClick={() => onOpenScene(scene.id)}
                      onKeyDown={(e) => handleRowKeyDown(scene.id, e)}
                      onDragStart={(e: DragEvent<HTMLDivElement>) => {
                        if (e.dataTransfer) {
                          e.dataTransfer.effectAllowed = 'move';
                          e.dataTransfer.setData('text/plain', scene.id);
                        }
                        beginDrag(scene.id);
                      }}
                      onDragEnd={endDrag}
                      onDragOver={
                        dragging && dragging !== scene.id
                          ? (e: DragEvent<HTMLDivElement>) => {
                              e.preventDefault();
                              setDropGroupKey(null);
                              const nextEdge = edgeFromPointer(e.currentTarget, e.clientY);
                              setDropTarget((prev) =>
                                prev && prev.sceneId === scene.id && prev.edge === nextEdge
                                  ? prev
                                  : { sceneId: scene.id, edge: nextEdge },
                              );
                            }
                          : undefined
                      }
                      onDrop={
                        dragging
                          ? (e: DragEvent<HTMLDivElement>) => {
                              e.preventDefault();
                              handleDropOnScene(scene, edgeFromPointer(e.currentTarget, e.clientY));
                            }
                          : undefined
                      }
                    >
                      <span className="mh-outline-scene-num">{numberOf.get(scene.id)}</span>
                      <span className="mh-outline-scene-heading">
                        {heading || t('editor.untitledScene')}
                      </span>
                      {summary && <span className="mh-outline-scene-summary">{summary}</span>}
                    </div>
                    {edge === 'after' && (
                      <div className="mh-drop-indicator after" aria-hidden="true" />
                    )}
                  </li>
                );
              })}
            </ol>
          )}
        </section>
      ))}

      {orphanChapters.length > 0 && (
        <section className="mh-outline-orphan-chapters" aria-label={t('editor.outlineUnstartedChapters')}>
          <h3 className="mh-outline-orphan-heading">{t('editor.outlineUnstartedChapters')}</h3>
          {orphanChapters.map((ch) => (
            <ChapterFallback
              key={ch.id}
              chapter={ch}
              converting={!!converting[ch.id]}
              onConvert={onConvert ?? (() => {})}
              onStartWriting={onStartWriting ?? (() => {})}
            />
          ))}
        </section>
      )}
    </div>
  );
}
