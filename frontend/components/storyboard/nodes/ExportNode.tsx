import React, { useCallback } from 'react';
import { NodeProps, Position } from '@xyflow/react';
import { FileOutput, Download, Loader2 } from 'lucide-react';
import NodeWrapper from './shared/NodeWrapper';
import { nodeControlStyles as s } from './shared/NodeControlStyles';
import { useStoryboardStore } from '../../../stores/storyboardStore';

// ─── Types ────────────────────────────────────────────────────────────────────

type ExportFormat = 'png' | 'pdf' | 'zip';

interface ExportOptions {
  frameNumbers: boolean;
  annotations: boolean;
  cameraOverlays: boolean;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const FORMAT_OPTIONS: { value: ExportFormat; label: string }[] = [
  { value: 'png', label: 'PNG (Images)' },
  { value: 'pdf', label: 'PDF (Document)' },
  { value: 'zip', label: 'ZIP (Archive)' },
];

const DEFAULT_OPTIONS: ExportOptions = {
  frameNumbers: true,
  annotations: true,
  cameraOverlays: false,
};

// ─── ExportNode ───────────────────────────────────────────────────────────────

const ExportNode = React.memo(function ExportNode({ id, selected, data }: NodeProps) {
  const { updateNodeData } = useStoryboardStore();

  const nodeData = data as Record<string, unknown>;
  const format = (nodeData.format as ExportFormat) ?? 'pdf';
  const options = (nodeData.options as ExportOptions) ?? DEFAULT_OPTIONS;
  const downloadUrl = nodeData.downloadUrl as string | undefined;
  const exporting = nodeData.exporting as boolean | undefined;
  const locked = nodeData.locked as boolean | undefined;

  const update = useCallback(
    (patch: Record<string, unknown>) => {
      updateNodeData(id, { data_json: { ...nodeData, ...patch } });
    },
    [id, nodeData, updateNodeData]
  );

  const toggleOption = useCallback(
    (key: keyof ExportOptions) => {
      update({ options: { ...options, [key]: !options[key] } });
    },
    [options, update]
  );

  const handleExport = useCallback(() => {
    update({ exporting: true, downloadUrl: undefined });
    // Simulate export — real implementation calls backend
    setTimeout(() => {
      update({
        exporting: false,
        downloadUrl: '#export-placeholder',
      });
    }, 2000);
  }, [update]);

  return (
    <NodeWrapper
      nodeId={id}
      title="Export"
      icon={<FileOutput size={14} />}
      selected={selected}
      locked={locked}
      accentColor="#ef4444"
      handles={[{ type: 'target', position: Position.Left }]}
    >
      {/* Format selector */}
      <div className={s.section}>
        <label className={s.label}>Format</label>
        <select
          className={s.modelSelector}
          value={format}
          onChange={(e) => update({ format: e.target.value as ExportFormat })}
        >
          {FORMAT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>

      {/* Options checkboxes */}
      <div className={s.section}>
        <label className={s.label}>Include</label>
        <div className="space-y-1.5">
          <CheckboxRow
            label="Frame Numbers"
            checked={options.frameNumbers}
            onChange={() => toggleOption('frameNumbers')}
          />
          <CheckboxRow
            label="Annotations"
            checked={options.annotations}
            onChange={() => toggleOption('annotations')}
          />
          <CheckboxRow
            label="Camera Overlays"
            checked={options.cameraOverlays}
            onChange={() => toggleOption('cameraOverlays')}
          />
        </div>
      </div>

      <button
        className={s.generateButton}
        onClick={handleExport}
        disabled={!!exporting}
      >
        {exporting ? (
          <>
            <Loader2 size={14} className="animate-spin" />
            Exporting...
          </>
        ) : (
          <>
            <FileOutput size={14} />
            Export as {format.toUpperCase()}
          </>
        )}
      </button>

      {downloadUrl && (
        <a
          href={downloadUrl}
          download
          className="flex items-center justify-center gap-2 w-full px-3 py-2 rounded-lg bg-green-600 hover:bg-green-700 text-sm font-medium text-white transition-colors"
        >
          <Download size={14} />
          Download Ready
        </a>
      )}
    </NodeWrapper>
  );
});

// ─── CheckboxRow ──────────────────────────────────────────────────────────────

interface CheckboxRowProps {
  label: string;
  checked: boolean;
  onChange: () => void;
}

function CheckboxRow({ label, checked, onChange }: CheckboxRowProps) {
  return (
    <label className="flex items-center gap-2 cursor-pointer group">
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        className="w-3.5 h-3.5 rounded border-gray-600 bg-gray-900 text-indigo-500 cursor-pointer"
      />
      <span className="text-xs text-gray-300 group-hover:text-gray-100 transition-colors">
        {label}
      </span>
    </label>
  );
}

export default ExportNode;
