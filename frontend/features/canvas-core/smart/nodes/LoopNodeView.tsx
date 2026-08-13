import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Image as ImageIcon, Plus, Square, TextCursorInput, Workflow, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { LoopMode, LoopNodeData } from '../types';
import { LOOP_MODE_TONE, SMART_NODE_DEFAULT_WIDTH } from '../types';
import { clampBatchSize, clampRoundStart, clampRounds } from '../loopVars';
import { startLoopRun } from '../loopRun';
import { useLoopRunStore } from '../loopRunStore';
import { resolveSourceUrls } from '../promptInputs';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { useCanvasReadOnly } from './useCanvasReadOnly';
import { useNodeDataPatch } from './useNodeDataPatch';

export function LoopNodeView({ id, data, selected }: NodeProps) {
  const { t } = useTranslation();
  const d = data as unknown as LoopNodeData;
  const patch = useNodeDataPatch(id);
  // Read-only: mode / toggles / prompts / counters all patch the node, and
  // Run dispatches generations. Stop goes with Run — a viewer cannot have
  // started the run it would stop.
  const readOnly = useCanvasReadOnly();
  const running = useLoopRunStore((s) => Boolean(s.running[id]));
  const stopping = useLoopRunStore((s) => s.running[id]?.stopRequested ?? false);

  // Legacy `mode:'batch'` renders as Serial (batch is no longer a run mode).
  const mode: LoopMode = d.mode === 'parallel' ? 'parallel' : 'serial';
  const tone = LOOP_MODE_TONE[mode];
  const showPrompt = d.show_prompt ?? true;
  const imageInput = d.image_input ?? false;
  const safeRounds = clampRounds(d.rounds ?? 1);
  const safeStart = clampRoundStart(d.round_start ?? 1);
  const safeBatch = clampBatchSize(d.image_batch_size ?? 1);
  const safePrompts = d.prompts && d.prompts.length > 0 ? d.prompts : [''];

  // Upstream durable images feeding this loop (IC preview + "will output N").
  // Selectors stay unconditional so the hook order never depends on
  // imageInput — the resolved list is simply unused when the toggle is off.
  const nodes = useCanvasCoreStore((s) => s.nodes);
  const connections = useCanvasCoreStore((s) => s.connections);
  const upstreamImages = imageInput ? resolveSourceUrls(String(id), nodes, connections) : [];

  const patchPrompt = (index: number, value: string) =>
    patch({ prompts: safePrompts.map((p, i) => (i === index ? value : p)) });
  const insertCounter = (index: number) =>
    patch({ prompts: safePrompts.map((p, i) => (i === index ? `${p}{{计数}}` : p)) });

  return (
    <div
      data-testid="smart-loop-node"
      className={`mh-node mh-loop-node ${tone} ${selected ? 'mh-node-selected' : ''}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.loop }}
    >
      <Handle type="target" position={Position.Left} />

      <div className="mh-loop-card">
        {/* Run mode segmented control */}
        <div className="mh-loop-seg">
          <button
            type="button"
            className={`nodrag mh-loop-seg-btn ${mode === 'serial' ? 'active' : ''}`}
            onClick={() => patch({ mode: 'serial' })}
            disabled={readOnly}
            aria-label="Serial"
          >
            {t('canvas.loopModeSerial')}
          </button>
          <button
            type="button"
            className={`nodrag mh-loop-seg-btn ${mode === 'parallel' ? 'active' : ''}`}
            onClick={() => patch({ mode: 'parallel' })}
            disabled={readOnly}
            aria-label="Parallel"
          >
            {t('canvas.loopModeParallel')}
          </button>
        </div>

        {/* Input toggles */}
        <div className="mh-loop-row">
          <button
            type="button"
            className={`nodrag mh-loop-toggle ${imageInput ? 'active' : ''}`}
            onClick={() => patch({ image_input: !imageInput })}
            disabled={readOnly}
            aria-label="Toggle image input"
          >
            <ImageIcon size={12} />
            <span>{t('canvas.loopToggleImage')}</span>
          </button>
          <button
            type="button"
            className={`nodrag mh-loop-toggle ${showPrompt ? 'active' : ''}`}
            onClick={() => patch({ show_prompt: !showPrompt })}
            disabled={readOnly}
            aria-label="Toggle prompt input"
          >
            <TextCursorInput size={12} />
            <span>{t('canvas.loopTogglePrompt')}</span>
          </button>
        </div>

        {/* Image panel */}
        {imageInput && (
          <div className="mh-loop-panel">
            <label className="mh-loop-mini">
              <span>{t('canvas.loopBatch')}</span>
              <input
                type="number"
                className="nodrag"
                min={1}
                max={100}
                value={safeBatch}
                onChange={(e) => patch({ image_batch_size: clampBatchSize(Number(e.target.value)) })}
                disabled={readOnly}
                aria-label={t('canvas.loopBatch')}
              />
            </label>
            <div className="mh-loop-note">
              {upstreamImages.length
                ? t('canvas.loopImageWillOutput', { n: upstreamImages.length })
                : t('canvas.loopImageEmpty')}
            </div>
          </div>
        )}

        {/* Prompt panel */}
        {showPrompt && (
          <div className="mh-loop-panel">
            <div className="mh-loop-prompt-list">
              {safePrompts.map((prompt, i) => (
                <div key={i} className="mh-loop-prompt-item">
                  <span className="mh-loop-prompt-index">{i + 1}</span>
                  <textarea
                    className="nodrag mh-loop-text read-only:opacity-80 read-only:cursor-default"
                    placeholder={t('canvas.loopPromptPlaceholder')}
                    value={prompt}
                    rows={1}
                    onChange={(e) => patchPrompt(i, e.target.value)}
                    readOnly={readOnly}
                    aria-label={`Loop prompt ${i + 1}`}
                  />
                  <button
                    type="button"
                    className="nodrag mh-loop-icon-btn"
                    onClick={() => patch({ prompts: safePrompts.filter((_p, j) => j !== i) })}
                    disabled={readOnly || safePrompts.length <= 1}
                    aria-label={`Remove prompt ${i + 1}`}
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
            </div>
            <div className="mh-loop-prompt-actions">
              <button
                type="button"
                className="nodrag mh-loop-token"
                onClick={() => insertCounter(safePrompts.length - 1)}
                disabled={readOnly}
                aria-label="Insert count token"
              >
                {t('canvas.loopCounterToken')}
              </button>
              <button
                type="button"
                className="nodrag mh-loop-add"
                onClick={() => patch({ prompts: [...safePrompts, ''] })}
                disabled={readOnly}
                aria-label="Add prompt"
              >
                <Plus size={12} />
              </button>
            </div>
          </div>
        )}

        {/* Footer: start / rounds / run */}
        <div className="mh-loop-footer">
          <label className="mh-loop-mini">
            <span>{t('canvas.loopStart')}</span>
            <input
              type="number"
              className="nodrag"
              min={1}
              max={9999}
              value={safeStart}
              onChange={(e) => patch({ round_start: clampRoundStart(Number(e.target.value)) })}
              disabled={readOnly}
              aria-label={t('canvas.loopStart')}
            />
          </label>
          <label className="mh-loop-mini">
            <span>{t('canvas.loopRounds')}</span>
            <input
              type="number"
              className="nodrag"
              min={1}
              max={100}
              value={safeRounds}
              onChange={(e) => patch({ rounds: clampRounds(Number(e.target.value)) })}
              disabled={readOnly}
              aria-label={t('canvas.loopRounds')}
            />
          </label>
          <button
            type="button"
            className={`nodrag mh-loop-run ${running ? 'is-stop' : ''}`}
            onClick={() => {
              if (running) {
                useLoopRunStore.getState().requestStop(id);
                return;
              }
              startLoopRun(id).catch((err) => console.error('[LoopNodeView] loop run failed:', err));
            }}
            disabled={stopping || readOnly}
            data-testid="loop-run"
            aria-label={running ? 'Stop loop' : 'Run loop'}
            title={running ? (stopping ? 'Stopping…' : 'Stop after this round') : t('canvas.loopRunAll')}
          >
            {running ? <Square size={11} /> : <Workflow size={11} />}
            <span>{running ? '' : t('canvas.loopRunAll')}</span>
          </button>
        </div>
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
