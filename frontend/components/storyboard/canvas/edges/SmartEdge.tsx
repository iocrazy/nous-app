import React, { useState, useCallback, useMemo } from 'react';
import {
  BaseEdge,
  EdgeProps,
  getBezierPath,
  getStraightPath,
  getSmoothStepPath,
  useReactFlow,
} from '@xyflow/react';
import { X } from 'lucide-react';
import { useStoryboardStore } from '../../../../stores/storyboardStore';

// ─── SmartEdge ────────────────────────────────────────────────────────────────

const SmartEdge = React.memo(function SmartEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  selected,
  markerEnd,
}: EdgeProps) {
  const [hovered, setHovered] = useState(false);
  const { setEdges } = useReactFlow();
  const edgeStyle = useStoryboardStore((s) => s.edgeStyle);

  const [edgePath, labelX, labelY] = useMemo(() => {
    const params = { sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition };

    if (edgeStyle === 'straight') {
      return getStraightPath(params);
    }
    if (edgeStyle === 'step') {
      return getSmoothStepPath({ ...params, borderRadius: 8 });
    }
    return getBezierPath(params);
  }, [edgeStyle, sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition]);

  const handleDelete = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      setEdges((edges) => edges.filter((edge) => edge.id !== id));
    },
    [id, setEdges]
  );

  const strokeColor = selected ? '#3b82f6' : '#6b7280';
  const strokeWidth = selected ? 2.5 : 1.5;

  return (
    <g
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* Wider invisible hit area */}
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={16}
        className="cursor-pointer"
      />
      <BaseEdge
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          stroke: strokeColor,
          strokeWidth,
          strokeDasharray: selected ? '5 3' : undefined,
          animation: selected ? 'dash 1s linear infinite' : undefined,
        }}
      />
      {(hovered || selected) && (
        <foreignObject
          x={labelX - 12}
          y={labelY - 12}
          width={24}
          height={24}
          className="overflow-visible"
        >
          <button
            onClick={handleDelete}
            className="flex items-center justify-center w-6 h-6 rounded-full bg-red-500 hover:bg-red-600 text-white shadow-md transition-colors"
            title="Delete edge"
          >
            <X size={12} />
          </button>
        </foreignObject>
      )}
    </g>
  );
});

export default SmartEdge;
