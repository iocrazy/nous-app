/**
 * Client-side storyboard frame merge — composes individual frame images
 * into a single grid image using an off-screen canvas.
 */

export interface MergeOptions {
  rows: number;
  cols: number;
  gap: number;
  padding: number;
  backgroundColor: string;
  showFrameNumbers: boolean;
  frameNumberColor: string;
  frameNumberSize: number;
  frameNumberPrefix: string;
  showNotes: boolean;
  noteColor: string;
  noteSize: number;
  notePlacement: 'overlay' | 'below';
}

export interface MergeFrameInput {
  imageUrl: string;
  note?: string;
}

const DEFAULT_MERGE_OPTIONS: MergeOptions = {
  rows: 2,
  cols: 2,
  gap: 8,
  padding: 0,
  backgroundColor: '#0f1115',
  showFrameNumbers: false,
  frameNumberColor: '#f8fafc',
  frameNumberSize: 24,
  frameNumberPrefix: 'S',
  showNotes: false,
  noteColor: '#f8fafc',
  noteSize: 14,
  notePlacement: 'overlay',
};

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Failed to load image: ${url}`));
    img.src = url;
  });
}

function wrapText(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
  lineHeight: number,
): string[] {
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let currentLine = '';

  for (const word of words) {
    const testLine = currentLine ? `${currentLine} ${word}` : word;
    const metrics = ctx.measureText(testLine);
    if (metrics.width > maxWidth && currentLine) {
      lines.push(currentLine);
      currentLine = word;
    } else {
      currentLine = testLine;
    }
  }
  if (currentLine) {
    lines.push(currentLine);
  }

  return lines.length > 0 ? lines : [''];
}

/**
 * Merge an array of frame images into a single grid image.
 * Returns a data URL of the merged PNG image.
 */
export async function mergeStoryboardFrames(
  frames: MergeFrameInput[],
  options: Partial<MergeOptions> = {},
): Promise<string> {
  const opts: MergeOptions = { ...DEFAULT_MERGE_OPTIONS, ...options };
  const { rows, cols, gap, padding, backgroundColor } = opts;

  if (frames.length === 0) {
    throw new Error('No frames to merge');
  }

  // Load all images in parallel
  const imageResults = await Promise.allSettled(
    frames.map((frame) => loadImage(frame.imageUrl)),
  );

  const images: Array<HTMLImageElement | null> = imageResults.map((result) =>
    result.status === 'fulfilled' ? result.value : null,
  );

  // Determine cell dimensions from the first successfully loaded image
  const firstImage = images.find((img): img is HTMLImageElement => img !== null);
  if (!firstImage) {
    throw new Error('Could not load any frame images');
  }

  const cellWidth = firstImage.naturalWidth;
  const cellHeight = firstImage.naturalHeight;

  // Calculate note area height when notes go below the frame
  const noteAreaHeight = opts.showNotes && opts.notePlacement === 'below'
    ? Math.round(opts.noteSize * 3)
    : 0;

  // Calculate total canvas dimensions
  const totalWidth = padding * 2 + cols * cellWidth + (cols - 1) * gap;
  const totalHeight = padding * 2 + rows * (cellHeight + noteAreaHeight) + (rows - 1) * gap;

  const canvas = document.createElement('canvas');
  canvas.width = totalWidth;
  canvas.height = totalHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    throw new Error('Could not create canvas 2D context');
  }

  // Fill background
  ctx.fillStyle = backgroundColor;
  ctx.fillRect(0, 0, totalWidth, totalHeight);

  // Draw each frame
  for (let i = 0; i < frames.length && i < rows * cols; i++) {
    const row = Math.floor(i / cols);
    const col = i % cols;
    const x = padding + col * (cellWidth + gap);
    const y = padding + row * (cellHeight + noteAreaHeight + gap);
    const img = images[i];

    if (img) {
      ctx.drawImage(img, x, y, cellWidth, cellHeight);
    } else {
      // Draw placeholder for failed images
      ctx.fillStyle = 'rgba(255,255,255,0.05)';
      ctx.fillRect(x, y, cellWidth, cellHeight);
    }

    // Draw frame number
    if (opts.showFrameNumbers) {
      const label = `${opts.frameNumberPrefix}${String(i + 1).padStart(2, '0')}`;
      ctx.font = `bold ${opts.frameNumberSize}px sans-serif`;
      ctx.fillStyle = 'rgba(0,0,0,0.55)';
      const labelMetrics = ctx.measureText(label);
      const labelPadX = 8;
      const labelPadY = 4;
      const labelBoxW = labelMetrics.width + labelPadX * 2;
      const labelBoxH = opts.frameNumberSize + labelPadY * 2;
      ctx.fillRect(x, y, labelBoxW, labelBoxH);

      ctx.fillStyle = opts.frameNumberColor;
      ctx.textBaseline = 'top';
      ctx.fillText(label, x + labelPadX, y + labelPadY);
    }

    // Draw note text
    const note = frames[i]?.note;
    if (opts.showNotes && note) {
      ctx.font = `${opts.noteSize}px sans-serif`;
      ctx.fillStyle = opts.noteColor;
      ctx.textBaseline = 'top';

      if (opts.notePlacement === 'overlay') {
        // Draw semi-transparent overlay at bottom of frame
        const overlayHeight = Math.round(opts.noteSize * 2.5);
        ctx.fillStyle = 'rgba(0,0,0,0.6)';
        ctx.fillRect(x, y + cellHeight - overlayHeight, cellWidth, overlayHeight);
        ctx.fillStyle = opts.noteColor;
        const lines = wrapText(ctx, note, cellWidth - 16, opts.noteSize * 1.3);
        const maxLines = Math.floor(overlayHeight / (opts.noteSize * 1.3));
        const visibleLines = lines.slice(0, maxLines);
        for (let li = 0; li < visibleLines.length; li++) {
          ctx.fillText(
            visibleLines[li],
            x + 8,
            y + cellHeight - overlayHeight + 6 + li * (opts.noteSize * 1.3),
          );
        }
      } else {
        // Draw note below frame
        const lines = wrapText(ctx, note, cellWidth - 16, opts.noteSize * 1.3);
        const maxLines = Math.floor(noteAreaHeight / (opts.noteSize * 1.3));
        const visibleLines = lines.slice(0, maxLines);
        for (let li = 0; li < visibleLines.length; li++) {
          ctx.fillText(
            visibleLines[li],
            x + 8,
            y + cellHeight + 4 + li * (opts.noteSize * 1.3),
          );
        }
      }
    }
  }

  return canvas.toDataURL('image/png');
}

/**
 * Trigger a browser download of a data URL.
 */
export function downloadDataUrl(dataUrl: string, filename: string): void {
  const link = document.createElement('a');
  link.href = dataUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}
