import React from 'react';
import { MiniMap } from '@xyflow/react';

// ─── CanvasMiniMap ────────────────────────────────────────────────────────────

const CanvasMiniMap = React.memo(function CanvasMiniMap() {
  return (
    <MiniMap
      className="!bottom-4 !right-4 z-10 rounded-lg overflow-hidden border border-gray-700"
      style={{
        backgroundColor: '#1f2937',
      }}
      nodeColor={(node) => {
        switch (node.type) {
          case 'upload':
            return '#3b82f6';
          case 'image_edit':
            return '#8b5cf6';
          case 'storyboard_split':
            return '#f59e0b';
          case 'storyboard_gen':
            return '#10b981';
          case 'text_annotation':
            return '#6b7280';
          case 'group':
            return '#374151';
          case 'export':
            return '#ef4444';
          case 'image_to_video':
            return '#ec4899';
          default:
            return '#6b7280';
        }
      }}
      maskColor="rgba(0,0,0,0.4)"
      pannable
      zoomable
    />
  );
});

export default CanvasMiniMap;
