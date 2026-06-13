import React from 'react';
import { Pencil, MoveRight, Square, Circle, Type, Undo2, Trash2, Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';

type AnnotationTool = 'pen' | 'arrow' | 'rect' | 'circle' | 'text';

interface AnnotationToolbarProps {
  activeTool: AnnotationTool;
  activeColor: string;
  strokeWidth: number;
  onToolChange: (tool: AnnotationTool) => void;
  onColorChange: (color: string) => void;
  onStrokeWidthChange: (width: number) => void;
  onUndo: () => void;
  onClear: () => void;
  onClose: () => void;
}

const TOOLS: { key: AnnotationTool; icon: React.ReactNode; labelKey: string }[] = [
  { key: 'pen', icon: <Pencil size={16} />, labelKey: 'annotations.pen' },
  { key: 'arrow', icon: <MoveRight size={16} />, labelKey: 'annotations.arrow' },
  { key: 'rect', icon: <Square size={16} />, labelKey: 'annotations.rectangle' },
  { key: 'circle', icon: <Circle size={16} />, labelKey: 'annotations.circle' },
  { key: 'text', icon: <Type size={16} />, labelKey: 'annotations.text' },
];

const PRESET_COLORS = [
  '#ef4444', // red
  '#3b82f6', // blue
  '#22c55e', // green
  '#eab308', // yellow
  '#ffffff', // white
  '#f97316', // orange
];

const STROKE_WIDTHS = [
  { value: 2, label: 'S' },
  { value: 4, label: 'M' },
  { value: 6, label: 'L' },
];

export const AnnotationToolbar: React.FC<AnnotationToolbarProps> = ({
  activeTool,
  activeColor,
  strokeWidth,
  onToolChange,
  onColorChange,
  onStrokeWidthChange,
  onUndo,
  onClear,
  onClose,
}) => {
  const { t } = useTranslation();

  return (
    <div className="absolute top-2 left-1/2 -translate-x-1/2 z-20 flex items-center gap-1 bg-ink-900/90 backdrop-blur border border-ink-700 rounded-xl px-3 py-2 shadow-2xl">
      {/* Tool buttons */}
      {TOOLS.map((toolItem) => (
        <button
          key={toolItem.key}
          onClick={() => onToolChange(toolItem.key)}
          className={`p-2 rounded-lg transition-colors ${
            activeTool === toolItem.key
              ? 'bg-indigo-500/30 text-indigo-300'
              : 'text-ink-400 hover:text-white hover:bg-ink-700'
          }`}
          title={t(toolItem.labelKey)}
        >
          {toolItem.icon}
        </button>
      ))}

      {/* Divider */}
      <div className="w-px h-6 bg-ink-700 mx-1" />

      {/* Color picker */}
      <div className="flex items-center gap-1" title={t('annotations.color')}>
        {PRESET_COLORS.map((c) => (
          <button
            key={c}
            onClick={() => onColorChange(c)}
            className={`w-5 h-5 rounded-full border-2 transition-all ${
              activeColor === c
                ? 'border-white scale-110'
                : 'border-ink-600 hover:border-ink-400'
            }`}
            style={{ backgroundColor: c }}
          />
        ))}
      </div>

      {/* Divider */}
      <div className="w-px h-6 bg-ink-700 mx-1" />

      {/* Stroke width */}
      <div className="flex items-center gap-0.5" title={t('annotations.strokeWidth')}>
        {STROKE_WIDTHS.map((sw) => (
          <button
            key={sw.value}
            onClick={() => onStrokeWidthChange(sw.value)}
            className={`w-7 h-7 rounded-lg flex items-center justify-center text-xs font-medium transition-colors ${
              strokeWidth === sw.value
                ? 'bg-indigo-500/30 text-indigo-300'
                : 'text-ink-400 hover:text-white hover:bg-ink-700'
            }`}
          >
            {sw.label}
          </button>
        ))}
      </div>

      {/* Divider */}
      <div className="w-px h-6 bg-ink-700 mx-1" />

      {/* Undo */}
      <button
        onClick={onUndo}
        className="p-2 rounded-lg text-ink-400 hover:text-white hover:bg-ink-700 transition-colors"
        title={t('annotations.undo')}
      >
        <Undo2 size={16} />
      </button>

      {/* Clear all */}
      <button
        onClick={onClear}
        className="p-2 rounded-lg text-ink-400 hover:text-red-400 hover:bg-ink-700 transition-colors"
        title={t('annotations.clearAll')}
      >
        <Trash2 size={16} />
      </button>

      {/* Divider */}
      <div className="w-px h-6 bg-ink-700 mx-1" />

      {/* Done / Close */}
      <button
        onClick={onClose}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 text-white hover:bg-indigo-500 transition-colors"
        title={t('annotations.done')}
      >
        <Check size={14} />
        {t('annotations.done')}
      </button>
    </div>
  );
};

export default AnnotationToolbar;
