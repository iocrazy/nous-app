import {
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  memo,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { useTranslation } from 'react-i18next';
import { GripVertical } from 'lucide-react';

import type { StoryboardGenNodeData } from '../../domain/canvasNodes';

// ─── Types ──────────────────────────────────────────────────────────────────

export interface FrameListProps {
  frames: StoryboardGenNodeData['frames'];
  gridCols: number;
  frameDescriptionDrafts: Record<string, string>;
  cellWidth: number;
  gridWidth: number;
  cellAspectRatio: string;
  incomingImageCount: number;
  /** Render highlighted description content (with reference tokens) */
  renderHighlight: (description: string) => ReactNode;
  onDescriptionChange: (index: number, description: string) => void;
  onKeyDown: (index: number, event: ReactKeyboardEvent<HTMLTextAreaElement>) => void;
  onReorder: (fromIndex: number, toIndex: number) => void;
  onPointerDown: (index: number, event: React.PointerEvent<HTMLTextAreaElement>) => void;
  onFocus: (index: number, event: React.FocusEvent<HTMLTextAreaElement>) => void;
  onScroll: (frameId: string) => void;
  /** Refs for highlight overlay sync */
  highlightRefs: React.MutableRefObject<Record<string, HTMLDivElement | null>>;
  textareaRefs: React.MutableRefObject<Record<string, HTMLTextAreaElement | null>>;
  /** Per-frame generation status */
  frameStatuses?: Record<string, FrameStatus>;
  /** Compact mode (hide descriptions, show thumbnails only) */
  compact?: boolean;
}

export type FrameStatus = 'idle' | 'pending' | 'generating' | 'done' | 'error';

// ─── Status indicator colors ────────────────────────────────────────────────

const STATUS_DOT_CLASS: Record<FrameStatus, string> = {
  idle: '',
  pending: 'bg-zinc-400',
  generating: 'bg-amber-400 animate-pulse',
  done: 'bg-emerald-400',
  error: 'bg-red-400',
};

// ─── Component ──────────────────────────────────────────────────────────────

export const FrameList = memo(function FrameList({
  frames,
  gridCols,
  frameDescriptionDrafts,
  cellWidth,
  gridWidth,
  cellAspectRatio,
  renderHighlight,
  onDescriptionChange,
  onKeyDown,
  onReorder,
  onPointerDown,
  onFocus,
  onScroll,
  highlightRefs,
  textareaRefs,
  frameStatuses,
  compact = false,
}: FrameListProps) {
  const { t } = useTranslation();

  // ─── Drag-to-reorder state ──────────────────────────────────────────

  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [overIndex, setOverIndex] = useState<number | null>(null);
  const [focusedIndex, setFocusedIndex] = useState<number | null>(null);
  const dragCounterRef = useRef(0);

  const handleDragStart = useCallback((index: number, e: React.DragEvent<HTMLDivElement>) => {
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(index));
    setDragIndex(index);
    dragCounterRef.current = 0;
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  }, []);

  const handleDragEnter = useCallback((index: number) => {
    dragCounterRef.current += 1;
    setOverIndex(index);
  }, []);

  const handleDragLeave = useCallback(() => {
    dragCounterRef.current -= 1;
    if (dragCounterRef.current <= 0) {
      setOverIndex(null);
      dragCounterRef.current = 0;
    }
  }, []);

  const handleDrop = useCallback((toIndex: number, e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const fromIndex = Number(e.dataTransfer.getData('text/plain'));
    if (!Number.isNaN(fromIndex) && fromIndex !== toIndex) {
      onReorder(fromIndex, toIndex);
    }
    setDragIndex(null);
    setOverIndex(null);
    dragCounterRef.current = 0;
  }, [onReorder]);

  const handleDragEnd = useCallback(() => {
    setDragIndex(null);
    setOverIndex(null);
    dragCounterRef.current = 0;
  }, []);

  // ─── Auto-grow textarea ─────────────────────────────────────────────

  const autoGrowTextarea = useCallback((el: HTMLTextAreaElement | null) => {
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, []);

  // ─── Focus tracking ────────────────────────────────────────────────

  const handleFocus = useCallback((index: number, e: React.FocusEvent<HTMLTextAreaElement>) => {
    setFocusedIndex(index);
    onFocus(index, e);
  }, [onFocus]);

  const handleBlur = useCallback(() => {
    setFocusedIndex(null);
  }, []);

  // ─── Render ─────────────────────────────────────────────────────────

  return (
    <div
      className="grid gap-0.5"
      style={{
        width: `${gridWidth}px`,
        gridTemplateColumns: `repeat(${gridCols}, ${cellWidth}px)`,
      }}
    >
      {frames.map((frame, index) => {
        const description = frameDescriptionDrafts[frame.id] ?? frame.description;
        const status = frameStatuses?.[frame.id] ?? 'idle';
        const isDragging = dragIndex === index;
        const isDropTarget = overIndex === index && dragIndex !== index;
        const isFocused = focusedIndex === index;
        const hasContent = description.trim().length > 0;
        const statusDot = STATUS_DOT_CLASS[status];

        return (
          <div
            key={frame.id}
            draggable
            onDragStart={(e) => handleDragStart(index, e)}
            onDragOver={handleDragOver}
            onDragEnter={() => handleDragEnter(index)}
            onDragLeave={handleDragLeave}
            onDrop={(e) => handleDrop(index, e)}
            onDragEnd={handleDragEnd}
            className={`group/frame relative overflow-hidden rounded border transition-all duration-150 ${
              isDragging
                ? 'opacity-40 border-zinc-600'
                : isDropTarget
                  ? 'border-indigo-400 ring-1 ring-indigo-400/30'
                  : isFocused
                    ? 'border-indigo-400/50 shadow-[0_0_0_1px_rgba(99,102,241,0.2)]'
                    : 'border-[rgba(255,255,255,0.06)] hover:border-[rgba(255,255,255,0.18)]'
            } bg-bg-dark/40 hover:bg-bg-dark/60`}
            style={{ aspectRatio: cellAspectRatio }}
          >
            {/* Drag handle */}
            <div className="absolute left-0 top-0 z-20 flex h-4 w-full cursor-grab items-center justify-center opacity-0 transition-opacity group-hover/frame:opacity-100 active:cursor-grabbing">
              <GripVertical className="h-2.5 w-2.5 text-text-muted/50" />
            </div>

            {/* Status dot */}
            {statusDot && (
              <div className={`absolute right-1 top-1 z-20 h-1.5 w-1.5 rounded-full ${statusDot}`} />
            )}

            {/* Highlight overlay */}
            <div
              ref={(el) => { highlightRefs.current[frame.id] = el; }}
              aria-hidden="true"
              className="ui-scrollbar pointer-events-none absolute inset-0 overflow-y-auto overflow-x-hidden text-[10px] leading-4 text-text-dark"
              style={{ scrollbarGutter: 'stable' }}
            >
              <div className="min-h-full whitespace-pre-wrap break-words px-1.5 py-1 text-left">
                {renderHighlight(description)}
              </div>
            </div>

            {/* Textarea (hidden text, visible caret) */}
            {compact ? (
              <div className="flex h-full w-full items-center justify-center text-[9px] text-text-muted/50">
                {hasContent ? `F${String(index + 1).padStart(2, '0')}` : (
                  <span className="border border-dashed border-[rgba(255,255,255,0.12)] rounded px-1.5 py-0.5 text-[8px] text-text-muted/30">
                    Add
                  </span>
                )}
              </div>
            ) : (
              <textarea
                ref={(el) => {
                  textareaRefs.current[frame.id] = el;
                  // Auto-grow is only meaningful if cell is tall enough
                }}
                value={description}
                onChange={(e) => {
                  onDescriptionChange(index, e.target.value);
                  autoGrowTextarea(e.currentTarget);
                }}
                onKeyDown={(e) => onKeyDown(index, e)}
                onScroll={() => onScroll(frame.id)}
                onPointerDown={(e) => onPointerDown(index, e as unknown as React.PointerEvent<HTMLTextAreaElement>)}
                onFocus={(e) => handleFocus(index, e)}
                onBlur={handleBlur}
                placeholder={!hasContent
                  ? t('node.storyboardGen.framePlaceholder', {
                      index: String(index + 1).padStart(2, '0'),
                      defaultValue: `Frame ${String(index + 1).padStart(2, '0')}`,
                    })
                  : undefined
                }
                wrap="soft"
                className="ui-scrollbar nodrag nowheel relative z-10 h-full w-full resize-none overflow-y-auto overflow-x-hidden bg-transparent px-1.5 py-1 text-left text-[10px] leading-4 text-transparent caret-text-dark placeholder:text-text-muted/40 focus:outline-none whitespace-pre-wrap break-words"
                style={{ scrollbarGutter: 'stable' }}
              />
            )}

            {/* Empty placeholder (dashed border) */}
            {!hasContent && !compact && !isFocused && (
              <div className="pointer-events-none absolute inset-1 z-0 flex items-center justify-center rounded border border-dashed border-[rgba(255,255,255,0.1)]">
                <span className="text-[8px] text-text-muted/25">
                  {t('node.storyboardGen.framePlaceholder', {
                    index: String(index + 1).padStart(2, '0'),
                    defaultValue: `Frame ${String(index + 1).padStart(2, '0')}`,
                  })}
                </span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
});
