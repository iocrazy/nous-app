import React from 'react';

// ─── Props ────────────────────────────────────────────────────────────────────

interface CameraOverlayProps {
  cameraMovement?: string;
  width?: number;
  height?: number;
  className?: string;
}

// ─── Arrow icon factories ─────────────────────────────────────────────────────

function ArrowDef({ id, color = 'black' }: { id: string; color?: string }) {
  return (
    <defs>
      <marker id={id} markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto">
        <path d="M0,1 L4,4 L0,7" fill="none" stroke={color} strokeWidth="1.5" />
      </marker>
    </defs>
  );
}

// ─── Movement overlays ────────────────────────────────────────────────────────

function PushOverlay() {
  return (
    <g>
      <ArrowDef id="arrowPush" color="black" />
      {/* center-out arrows */}
      <line x1="50" y1="50" x2="50" y2="15" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowPush)" opacity="0.9" />
      <line x1="50" y1="50" x2="50" y2="85" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowPush)" opacity="0.9" />
      <line x1="50" y1="50" x2="15" y2="50" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowPush)" opacity="0.9" />
      <line x1="50" y1="50" x2="85" y2="50" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowPush)" opacity="0.9" />
    </g>
  );
}

function PullOverlay() {
  return (
    <g>
      <ArrowDef id="arrowPull" color="black" />
      {/* outside-in arrows */}
      <line x1="50" y1="15" x2="50" y2="45" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowPull)" opacity="0.9" />
      <line x1="50" y1="85" x2="50" y2="55" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowPull)" opacity="0.9" />
      <line x1="15" y1="50" x2="45" y2="50" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowPull)" opacity="0.9" />
      <line x1="85" y1="50" x2="55" y2="50" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowPull)" opacity="0.9" />
    </g>
  );
}

function PanOverlay() {
  return (
    <g>
      <ArrowDef id="arrowPan" color="black" />
      <line x1="15" y1="50" x2="85" y2="50" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowPan)" opacity="0.9" />
      <line x1="85" y1="50" x2="15" y2="50" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowPan)" opacity="0.9" />
    </g>
  );
}

function TiltOverlay() {
  return (
    <g>
      <ArrowDef id="arrowTilt" color="black" />
      <line x1="50" y1="15" x2="50" y2="85" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowTilt)" opacity="0.9" />
      <line x1="50" y1="85" x2="50" y2="15" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowTilt)" opacity="0.9" />
    </g>
  );
}

function DollyOverlay() {
  return (
    <g>
      <ArrowDef id="arrowDolly" color="black" />
      {/* Curved arc suggesting dolly track */}
      <path d="M 20 70 Q 50 30 80 70" stroke="white" strokeWidth="2.5" fill="none" opacity="0.9" />
      <line x1="65" y1="55" x2="80" y2="70" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowDolly)" opacity="0.9" />
    </g>
  );
}

function CraneOverlay() {
  return (
    <g>
      <ArrowDef id="arrowCrane" color="black" />
      {/* Arc from low-left to high-right */}
      <path d="M 20 75 Q 35 20 75 20" stroke="white" strokeWidth="2.5" fill="none" opacity="0.9" />
      <line x1="60" y1="20" x2="75" y2="20" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowCrane)" opacity="0.9" />
    </g>
  );
}

function TrackingOverlay() {
  return (
    <g>
      <ArrowDef id="arrowTracking" color="black" />
      {/* Parallel lines suggesting tracking */}
      <line x1="15" y1="40" x2="85" y2="40" stroke="white" strokeWidth="1.5" strokeDasharray="4 3" opacity="0.6" />
      <line x1="15" y1="60" x2="85" y2="60" stroke="white" strokeWidth="1.5" strokeDasharray="4 3" opacity="0.6" />
      <line x1="40" y1="50" x2="65" y2="50" stroke="white" strokeWidth="2.5" markerEnd="url(#arrowTracking)" opacity="0.9" />
    </g>
  );
}

function StaticOverlay() {
  return (
    <g>
      {/* X mark for static */}
      <circle cx="50" cy="50" r="12" stroke="white" strokeWidth="2" fill="none" opacity="0.7" />
      <line x1="44" y1="44" x2="56" y2="56" stroke="white" strokeWidth="2" opacity="0.7" />
      <line x1="56" y1="44" x2="44" y2="56" stroke="white" strokeWidth="2" opacity="0.7" />
    </g>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

const OVERLAY_MAP: Record<string, React.ReactNode> = {
  Push: <PushOverlay />,
  Pull: <PullOverlay />,
  Pan: <PanOverlay />,
  Tilt: <TiltOverlay />,
  Dolly: <DollyOverlay />,
  Crane: <CraneOverlay />,
  Tracking: <TrackingOverlay />,
  Static: <StaticOverlay />,
};

const CameraOverlay = React.memo(function CameraOverlay({
  cameraMovement,
  width = 100,
  height = 100,
  className = '',
}: CameraOverlayProps) {
  if (!cameraMovement || !OVERLAY_MAP[cameraMovement]) return null;

  return (
    <svg
      width={width}
      height={height}
      viewBox="0 0 100 100"
      className={['absolute inset-0 pointer-events-none', className].join(' ')}
      style={{ filter: 'drop-shadow(0 0 1px black)' }}
    >
      {OVERLAY_MAP[cameraMovement]}
    </svg>
  );
});

export default CameraOverlay;
