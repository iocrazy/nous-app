// features/canvas-core/smart/nodes/PanoramaViewer.tsx
//
// IC 360° panorama preview (smart-canvas.js ensurePanoramaRenderer /
// renderPanoramaFrame): a three.js sphere with the equirect image as its
// inside texture. Drag = yaw/pitch, wheel = fov (30–100), Export captures
// the current view as a PNG blob. three loads lazily — the canvas bundle
// doesn't pay for WebGL until a panorama is actually opened.

import { useEffect, useRef, useState } from 'react';

interface PanoramaViewerProps {
  src: string;
  /** Current view exported as PNG (IC exportPanoramaFrame). */
  onExport?: (blob: Blob, name: string) => void;
  exportName?: string;
}

export function PanoramaViewer({ src, onExport, exportName }: PanoramaViewerProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [fov, setFov] = useState(75);
  const apiRef = useRef<{
    setFov(v: number): void;
    drag(dx: number, dy: number): void;
    capture(): string | null;
    dispose(): void;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    const host = hostRef.current;
    if (!host) return;
    void (async () => {
      try {
        const THREE = await import('three');
        if (cancelled || !hostRef.current) return;
        const width = host.clientWidth || 800;
        const height = host.clientHeight || 450;
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(75, width / height, 0.1, 1100);
        const renderer = new THREE.WebGLRenderer({
          antialias: true,
          // Export needs the buffer to survive until toDataURL.
          preserveDrawingBuffer: true,
        });
        renderer.setSize(width, height);
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        host.appendChild(renderer.domElement);

        const geometry = new THREE.SphereGeometry(500, 60, 40);
        geometry.scale(-1, 1, 1); // view from inside
        const texture = new THREE.TextureLoader().load(src, undefined, undefined, () =>
          setError('Failed to load panorama texture'),
        );
        texture.colorSpace = THREE.SRGBColorSpace;
        const mesh = new THREE.Mesh(
          geometry,
          new THREE.MeshBasicMaterial({ map: texture }),
        );
        scene.add(mesh);

        let lon = 0;
        let lat = 0;
        const render = () => {
          lat = Math.max(-85, Math.min(85, lat));
          const phi = THREE.MathUtils.degToRad(90 - lat);
          const theta = THREE.MathUtils.degToRad(lon);
          camera.lookAt(
            500 * Math.sin(phi) * Math.cos(theta),
            500 * Math.cos(phi),
            500 * Math.sin(phi) * Math.sin(theta),
          );
          renderer.render(scene, camera);
        };
        let raf = 0;
        const loop = () => {
          render();
          raf = requestAnimationFrame(loop);
        };
        loop();

        apiRef.current = {
          setFov(v) {
            camera.fov = v;
            camera.updateProjectionMatrix();
          },
          drag(dx, dy) {
            lon -= dx * 0.15;
            lat += dy * 0.15;
          },
          capture() {
            render();
            return renderer.domElement.toDataURL('image/png');
          },
          dispose() {
            cancelAnimationFrame(raf);
            texture.dispose();
            geometry.dispose();
            renderer.dispose();
            renderer.domElement.remove();
          },
        };
      } catch (err) {
        console.error('[panorama] three init failed', err);
        setError('WebGL unavailable');
      }
    })();
    return () => {
      cancelled = true;
      apiRef.current?.dispose();
      apiRef.current = null;
    };
  }, [src]);

  const draggingRef = useRef<{ x: number; y: number } | null>(null);

  return (
    <div
      data-testid="panorama-viewer"
      className="relative h-full w-full cursor-grab active:cursor-grabbing"
      ref={hostRef}
      onPointerDown={(e) => {
        draggingRef.current = { x: e.clientX, y: e.clientY };
        (e.target as Element).setPointerCapture?.(e.pointerId);
      }}
      onPointerMove={(e) => {
        const d = draggingRef.current;
        if (!d) return;
        apiRef.current?.drag(e.clientX - d.x, e.clientY - d.y);
        draggingRef.current = { x: e.clientX, y: e.clientY };
      }}
      onPointerUp={() => (draggingRef.current = null)}
      onWheel={(e) => {
        const next = Math.min(100, Math.max(30, fov + (e.deltaY > 0 ? 5 : -5)));
        setFov(next);
        apiRef.current?.setFov(next);
      }}
    >
      {error && (
        <div className="absolute inset-0 flex items-center justify-center text-xs text-rose-400">
          {error}
        </div>
      )}
      <span className="pointer-events-none absolute bottom-2 left-2 rounded-md bg-black/50 px-1.5 py-0.5 text-[10px] font-semibold text-white">
        {Math.round((75 / fov) * 100)}%
      </span>
      {onExport && (
        <button
          type="button"
          data-testid="panorama-export"
          onClick={() => {
            const dataUrl = apiRef.current?.capture();
            if (!dataUrl) return;
            void fetch(dataUrl)
              .then((r) => r.blob())
              .then((blob) => onExport(blob, exportName ?? 'panorama-view.png'));
          }}
          className="absolute right-2 top-2 rounded-lg bg-black/50 px-2 py-1 text-[11px] font-semibold text-white hover:bg-black/70"
        >
          Export View
        </button>
      )}
    </div>
  );
}
