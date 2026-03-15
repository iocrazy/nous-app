import React from 'react';
import { Image, FileText, Archive } from 'lucide-react';

// ─── Props ────────────────────────────────────────────────────────────────────

interface ExportPreviewProps {
  format: 'png' | 'pdf' | 'zip';
  columns?: number;
  includeFrameNumbers?: boolean;
  frameCount?: number;
}

// ─── Sub-previews ─────────────────────────────────────────────────────────────

function PngGridPreview({ columns, frameCount, includeFrameNumbers }: {
  columns: number;
  frameCount: number;
  includeFrameNumbers: boolean;
}) {
  const rows = Math.ceil(frameCount / columns);
  const cells = Array.from({ length: Math.min(frameCount, columns * Math.min(rows, 3)) });

  return (
    <div className="p-4">
      <div
        className="grid gap-1"
        style={{ gridTemplateColumns: `repeat(${Math.min(columns, 6)}, 1fr)` }}
      >
        {cells.map((_, i) => (
          <div key={i} className="relative bg-gray-700 rounded aspect-video flex items-center justify-center">
            <Image size={10} className="text-gray-600" />
            {includeFrameNumbers && (
              <span className="absolute top-0.5 left-0.5 text-[6px] text-gray-500 font-mono">
                {i + 1}
              </span>
            )}
          </div>
        ))}
      </div>
      {frameCount > cells.length && (
        <p className="text-[10px] text-gray-600 text-center mt-2">
          +{frameCount - cells.length} more frames
        </p>
      )}
    </div>
  );
}

function PdfPagePreview({ includeMetadata }: { includeMetadata: boolean }) {
  return (
    <div className="p-4 flex gap-3 justify-center">
      {/* Cover page */}
      <div className="w-20 h-28 bg-gray-700 rounded border border-gray-600 flex flex-col items-center justify-center gap-1">
        <FileText size={16} className="text-gray-500" />
        <div className="w-12 h-1 bg-gray-600 rounded" />
        <div className="w-8 h-0.5 bg-gray-700 rounded" />
      </div>

      {/* Content page */}
      <div className="w-20 h-28 bg-gray-700 rounded border border-gray-600 p-1.5 flex flex-col gap-1">
        <div className="bg-gray-600 rounded h-12 flex items-center justify-center">
          <Image size={10} className="text-gray-500" />
        </div>
        {includeMetadata && (
          <>
            <div className="w-full h-0.5 bg-gray-600 rounded" />
            <div className="w-10 h-0.5 bg-gray-600 rounded" />
          </>
        )}
      </div>
    </div>
  );
}

function ZipPreview() {
  return (
    <div className="p-6 flex flex-col items-center gap-2">
      <Archive size={32} className="text-gray-600" />
      <p className="text-xs text-gray-500">All frames + assets bundled</p>
      <div className="text-[10px] text-gray-600 text-left space-y-0.5">
        <p>📁 frames/</p>
        <p className="ml-4">🖼 frame_001.png</p>
        <p className="ml-4">🖼 frame_002.png</p>
        <p>📁 characters/</p>
        <p>📄 metadata.json</p>
      </div>
    </div>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

const ExportPreview = React.memo(function ExportPreview({
  format,
  columns = 3,
  includeFrameNumbers = true,
  frameCount = 6,
}: ExportPreviewProps) {
  return (
    <div className="bg-gray-950 rounded-xl border border-gray-800 min-h-[160px] flex items-center justify-center">
      {format === 'png' && (
        <PngGridPreview
          columns={columns}
          frameCount={frameCount}
          includeFrameNumbers={includeFrameNumbers}
        />
      )}
      {format === 'pdf' && (
        <PdfPagePreview includeMetadata={includeFrameNumbers} />
      )}
      {format === 'zip' && <ZipPreview />}
    </div>
  );
});

export default ExportPreview;
