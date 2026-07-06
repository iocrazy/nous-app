/**
 * SceneFlowNode — the scene card rendered inside the node view (Phase B Task 2).
 *
 * Presents a scene as: a number badge + an `INT · Location · TIME` heading row,
 * a one-line first-action summary, and an element-count pill. It draws ONLY from
 * the editor shell's CSS-variable design system (mh-flow-* classes defined in
 * editorShellStyles.ts) so it themes with the rest of the v2 editor — it does not
 * pull in features/script's canvas CSS, which belongs to the legacy node editor.
 */
import { memo, useContext } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import type { SceneNodeData } from './sceneNodeMapper';
import { ScenePresenceContext } from '../collab/scenePresenceContext';
import { ScenePresenceBadge } from '../collab/ScenePresenceBadge';

function SceneFlowNodeImpl({ data }: NodeProps) {
  const { t } = useTranslation();
  const { scene, summary, coverUrl } = data as unknown as SceneNodeData;
  const presenceByScene = useContext(ScenePresenceContext);
  const focusPresence = presenceByScene[String(scene.id)] ?? [];

  const num = (scene.sort_order ?? 0) + 1;
  const heading = [scene.heading_int_ext, scene.location_text, scene.time_of_day]
    .map((part) => (part ?? '').trim())
    .filter((part) => part.length > 0)
    .join(' · ');
  const elementCount = scene.elements.length;

  return (
    <div className="mh-flow-node mh-flow-scene" data-scene-id={scene.id}>
      <Handle type="target" position={Position.Top} className="mh-flow-handle" />
      {coverUrl && (
        <img
          className="mh-flow-scene-cover"
          src={coverUrl}
          alt={t('editor.nodesShotCoverAlt')}
          loading="lazy"
        />
      )}
      <div className="mh-flow-scene-head">
        <span className="mh-scene-num-badge">{num}</span>
        <span className="mh-flow-scene-heading">
          {heading || t('editor.nodesUntitledScene')}
        </span>
        <ScenePresenceBadge users={focusPresence} />
      </div>
      <div className="mh-flow-scene-summary">
        {summary || t('editor.nodesEmptyScene')}
      </div>
      <div className="mh-flow-scene-foot">
        <span className="mh-flow-pill">
          {t('editor.nodesElementCount', { count: elementCount })}
        </span>
      </div>
      <Handle type="source" position={Position.Bottom} className="mh-flow-handle" />
    </div>
  );
}

export const SceneFlowNode = memo(SceneFlowNodeImpl);
