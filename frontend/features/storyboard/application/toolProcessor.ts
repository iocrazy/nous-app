// Web-based tool processor for canvas image operations.
// Replaces Tauri-specific commands with HTML Canvas + backend API calls.

import {
  NODE_TOOL_TYPES,
  type NodeToolType,
  type StoryboardFrameItem,
} from '../domain/canvasNodes';
import {
  canvasToDataUrl,
  detectAspectRatio,
  loadImageElement,
  parseAspectRatio,
  persistImageLocally,
} from './imageData';
import type {
  IdGenerator,
  ImageSplitGateway,
  ToolProcessor,
  ToolProcessorResult,
} from './ports';

export class CanvasToolProcessor implements ToolProcessor {
  constructor(
    private readonly splitGateway: ImageSplitGateway,
    private readonly idGenerator: IdGenerator,
  ) {}

  async process(
    toolType: NodeToolType,
    sourceImageUrl: string,
    options: Record<string, unknown>,
  ): Promise<ToolProcessorResult> {
    if (toolType === NODE_TOOL_TYPES.splitStoryboard) {
      return await this.splitStoryboard(
        sourceImageUrl,
        Number(options.rows ?? 3),
        Number(options.cols ?? 3),
        Number(options.lineThicknessPercent),
        Number(options.lineThickness ?? 0),
      );
    }

    switch (toolType) {
      case NODE_TOOL_TYPES.crop:
        return {
          outputImageUrl: await this.cropImage(sourceImageUrl, options),
        };
      case NODE_TOOL_TYPES.annotate:
        return {
          outputImageUrl: await this.annotateImage(sourceImageUrl, options),
        };
      default:
        throw new Error(`Unsupported tool type: ${String(toolType)}`);
    }
  }

  // ─── Crop ────────────────────────────────────────────────────────────────────

  private async cropImage(
    sourceImage: string,
    options: Record<string, unknown>,
  ): Promise<string> {
    const aspectRatio = String(options.aspectRatio ?? '1:1');
    const targetRatio = parseAspectRatio(aspectRatio);
    const image = await loadImageElement(sourceImage);

    const cropX = Number(options.cropX);
    const cropY = Number(options.cropY);
    const cropWidthOption = Number(options.cropWidth);
    const cropHeightOption = Number(options.cropHeight);

    const hasManualCropArea =
      Number.isFinite(cropX) &&
      Number.isFinite(cropY) &&
      Number.isFinite(cropWidthOption) &&
      Number.isFinite(cropHeightOption) &&
      cropWidthOption > 0 &&
      cropHeightOption > 0;

    let cropWidth = image.naturalWidth;
    let cropHeight = image.naturalHeight;
    let offsetX = 0;
    let offsetY = 0;

    if (hasManualCropArea) {
      offsetX = Math.min(image.naturalWidth - 1, Math.max(0, Math.floor(cropX)));
      offsetY = Math.min(image.naturalHeight - 1, Math.max(0, Math.floor(cropY)));
      cropWidth = Math.max(1, Math.min(Math.floor(cropWidthOption), image.naturalWidth - offsetX));
      cropHeight = Math.max(1, Math.min(Math.floor(cropHeightOption), image.naturalHeight - offsetY));
    } else if (aspectRatio === 'free') {
      offsetX = 0;
      offsetY = 0;
      cropWidth = image.naturalWidth;
      cropHeight = image.naturalHeight;
    } else {
      const sourceRatio = image.naturalWidth / image.naturalHeight;
      if (sourceRatio > targetRatio) {
        cropWidth = image.naturalHeight * targetRatio;
      } else {
        cropHeight = image.naturalWidth / targetRatio;
      }

      offsetX = (image.naturalWidth - cropWidth) / 2;
      offsetY = (image.naturalHeight - cropHeight) / 2;
    }

    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.floor(cropWidth));
    canvas.height = Math.max(1, Math.floor(cropHeight));

    const context = canvas.getContext('2d');
    if (!context) {
      throw new Error('Failed to initialize canvas context');
    }

    context.drawImage(
      image,
      offsetX,
      offsetY,
      cropWidth,
      cropHeight,
      0,
      0,
      canvas.width,
      canvas.height,
    );

    return canvasToDataUrl(canvas);
  }

  // ─── Annotate ────────────────────────────────────────────────────────────────

  private async annotateImage(
    sourceImage: string,
    options: Record<string, unknown>,
  ): Promise<string> {
    const image = await loadImageElement(sourceImage);
    const canvas = document.createElement('canvas');
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;

    const context = canvas.getContext('2d');
    if (!context) {
      throw new Error('Failed to initialize canvas context');
    }

    context.drawImage(image, 0, 0, canvas.width, canvas.height);

    const text = String(options.text ?? '').trim();
    const position = String(options.position ?? 'bottom');
    const color = String(options.color ?? '#FFFFFF');

    if (!text) {
      return canvasToDataUrl(canvas);
    }

    const fontSize = Math.max(24, Math.round(canvas.width * 0.04));
    context.font = `600 ${fontSize}px sans-serif`;
    context.textAlign = 'center';
    context.textBaseline = 'middle';

    const textWidth = context.measureText(text).width;
    const paddingX = Math.round(fontSize * 0.8);
    const paddingY = Math.round(fontSize * 0.6);
    const boxWidth = textWidth + paddingX * 2;
    const boxHeight = fontSize + paddingY * 2;

    const x = canvas.width / 2;
    const y = this.resolveAnnotateY(position, canvas.height, boxHeight);

    context.fillStyle = 'rgba(0, 0, 0, 0.45)';
    context.fillRect(x - boxWidth / 2, y - boxHeight / 2, boxWidth, boxHeight);
    context.fillStyle = color;
    context.fillText(text, x, y);

    return canvasToDataUrl(canvas);
  }

  private resolveAnnotateY(position: string, canvasHeight: number, boxHeight: number): number {
    if (position === 'top') {
      return boxHeight / 2 + 24;
    }
    if (position === 'center') {
      return canvasHeight / 2;
    }
    return canvasHeight - boxHeight / 2 - 24;
  }

  // ─── Split storyboard ────────────────────────────────────────────────────────

  private async splitStoryboard(
    sourceImage: string,
    rows: number,
    cols: number,
    lineThicknessPercent: number,
    lineThicknessPxFallback: number,
  ): Promise<ToolProcessorResult> {
    const safeRows = Math.max(1, Math.floor(Number.isFinite(rows) ? rows : 3));
    const safeCols = Math.max(1, Math.floor(Number.isFinite(cols) ? cols : 3));
    const safeLineThickness = await this.resolveSplitLineThicknessPx(
      sourceImage,
      safeRows,
      safeCols,
      lineThicknessPercent,
      lineThicknessPxFallback,
    );

    let outputs: string[];
    try {
      outputs = await this.splitGateway.split(
        sourceImage,
        safeRows,
        safeCols,
        safeLineThickness,
      );
    } catch {
      // Fallback to client-side canvas split when backend is unavailable.
      outputs = await this.localSplit(sourceImage, safeRows, safeCols, safeLineThickness);
    }

    const persistedFrameImages = await Promise.all(
      outputs.map(async (imageUrl) => await persistImageLocally(imageUrl)),
    );

    let frameAspectRatio: string | undefined;
    const firstFrameImage = persistedFrameImages[0];
    if (firstFrameImage) {
      try {
        frameAspectRatio = await detectAspectRatio(firstFrameImage);
      } catch {
        frameAspectRatio = undefined;
      }
    }

    const resolvedFrameAspectRatio = frameAspectRatio ?? `${safeCols}:${safeRows}`;
    const frames: StoryboardFrameItem[] = persistedFrameImages.map((imageUrl, index) => ({
      id: this.idGenerator.next(),
      imageUrl,
      previewImageUrl: imageUrl,
      aspectRatio: resolvedFrameAspectRatio,
      note: '',
      order: index,
    }));

    return {
      storyboardFrames: frames,
      rows: safeRows,
      cols: safeCols,
      frameAspectRatio: resolvedFrameAspectRatio,
    };
  }

  // ─── Split helpers ─────────────────────────────────────────────────────────

  private resolveMaxAllowedLineThickness(
    imageWidth: number,
    imageHeight: number,
    rows: number,
    cols: number,
  ): number {
    const maxLineByWidth =
      cols > 1 ? Math.floor((imageWidth - cols) / (cols - 1)) : Number.MAX_SAFE_INTEGER;
    const maxLineByHeight =
      rows > 1 ? Math.floor((imageHeight - rows) / (rows - 1)) : Number.MAX_SAFE_INTEGER;
    return Math.max(0, Math.min(maxLineByWidth, maxLineByHeight));
  }

  private async resolveSplitLineThicknessPx(
    sourceImage: string,
    rows: number,
    cols: number,
    lineThicknessPercent: number,
    lineThicknessPxFallback: number,
  ): Promise<number> {
    if (!Number.isFinite(lineThicknessPercent)) {
      return Math.max(0, Math.floor(lineThicknessPxFallback));
    }

    const normalizedPercent = Math.max(0, lineThicknessPercent);
    if (normalizedPercent <= 0) {
      return 0;
    }

    const image = await loadImageElement(sourceImage);
    const imageWidth = Math.max(1, image.naturalWidth);
    const imageHeight = Math.max(1, image.naturalHeight);
    const basis = Math.max(1, Math.min(imageWidth, imageHeight));
    const rawPixelThickness = Math.max(1, Math.round((basis * normalizedPercent) / 100));
    const maxAllowedThickness = this.resolveMaxAllowedLineThickness(
      imageWidth,
      imageHeight,
      rows,
      cols,
    );
    return Math.max(0, Math.min(rawPixelThickness, maxAllowedThickness));
  }

  private splitIntoSegments(totalSize: number, segmentCount: number): number[] {
    const baseSize = Math.floor(totalSize / segmentCount);
    const remainder = totalSize % segmentCount;

    return Array.from(
      { length: segmentCount },
      (_item, index) => baseSize + (index < remainder ? 1 : 0),
    );
  }

  private async localSplit(
    sourceImage: string,
    rows: number,
    cols: number,
    lineThickness: number,
  ): Promise<string[]> {
    const image = await loadImageElement(sourceImage);

    const maxAllowedLine = this.resolveMaxAllowedLineThickness(
      image.naturalWidth,
      image.naturalHeight,
      rows,
      cols,
    );
    const resolvedLineThickness = Math.min(Math.max(0, lineThickness), maxAllowedLine);

    const usableWidth = image.naturalWidth - (cols - 1) * resolvedLineThickness;
    const usableHeight = image.naturalHeight - (rows - 1) * resolvedLineThickness;

    if (usableWidth < cols || usableHeight < rows) {
      throw new Error('Line thickness too large for the given grid dimensions');
    }

    const columnWidths = this.splitIntoSegments(usableWidth, cols);
    const rowHeights = this.splitIntoSegments(usableHeight, rows);

    const results: string[] = [];

    const yOffsets: number[] = [];
    let yCursor = 0;
    for (let row = 0; row < rows; row += 1) {
      yOffsets.push(yCursor);
      yCursor += rowHeights[row];
      if (row < rows - 1) {
        yCursor += resolvedLineThickness;
      }
    }

    const xOffsets: number[] = [];
    let xCursor = 0;
    for (let col = 0; col < cols; col += 1) {
      xOffsets.push(xCursor);
      xCursor += columnWidths[col];
      if (col < cols - 1) {
        xCursor += resolvedLineThickness;
      }
    }

    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < cols; col += 1) {
        const targetWidth = columnWidths[col];
        const targetHeight = rowHeights[row];

        const canvas = document.createElement('canvas');
        canvas.width = targetWidth;
        canvas.height = targetHeight;

        const context = canvas.getContext('2d');
        if (!context) {
          throw new Error('Failed to initialize canvas context');
        }

        context.drawImage(
          image,
          xOffsets[col],
          yOffsets[row],
          targetWidth,
          targetHeight,
          0,
          0,
          targetWidth,
          targetHeight,
        );
        results.push(canvasToDataUrl(canvas));
      }
    }

    return results;
  }
}

// ─── Standalone functions (for direct use without class instantiation) ──────

/**
 * Crop an image using HTML Canvas. Returns a data URL.
 */
export async function cropImageSource(
  imageUrl: string,
  cropRect: { x: number; y: number; width: number; height: number },
): Promise<string> {
  const image = await loadImageElement(imageUrl);

  const offsetX = Math.min(image.naturalWidth - 1, Math.max(0, Math.floor(cropRect.x)));
  const offsetY = Math.min(image.naturalHeight - 1, Math.max(0, Math.floor(cropRect.y)));
  const cropWidth = Math.max(1, Math.min(Math.floor(cropRect.width), image.naturalWidth - offsetX));
  const cropHeight = Math.max(
    1,
    Math.min(Math.floor(cropRect.height), image.naturalHeight - offsetY),
  );

  const canvas = document.createElement('canvas');
  canvas.width = cropWidth;
  canvas.height = cropHeight;

  const context = canvas.getContext('2d');
  if (!context) {
    throw new Error('Failed to initialize canvas context');
  }

  context.drawImage(image, offsetX, offsetY, cropWidth, cropHeight, 0, 0, cropWidth, cropHeight);

  return canvasToDataUrl(canvas);
}

/**
 * Annotate an image with text overlay using HTML Canvas. Returns a data URL.
 */
export async function annotateImageSource(
  imageUrl: string,
  text: string,
  options?: { position?: 'top' | 'center' | 'bottom'; color?: string },
): Promise<string> {
  const image = await loadImageElement(imageUrl);
  const canvas = document.createElement('canvas');
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;

  const context = canvas.getContext('2d');
  if (!context) {
    throw new Error('Failed to initialize canvas context');
  }

  context.drawImage(image, 0, 0, canvas.width, canvas.height);

  if (!text.trim()) {
    return canvasToDataUrl(canvas);
  }

  const position = options?.position ?? 'bottom';
  const color = options?.color ?? '#FFFFFF';
  const fontSize = Math.max(24, Math.round(canvas.width * 0.04));

  context.font = `600 ${fontSize}px sans-serif`;
  context.textAlign = 'center';
  context.textBaseline = 'middle';

  const textWidth = context.measureText(text).width;
  const paddingX = Math.round(fontSize * 0.8);
  const paddingY = Math.round(fontSize * 0.6);
  const boxWidth = textWidth + paddingX * 2;
  const boxHeight = fontSize + paddingY * 2;

  const x = canvas.width / 2;
  let y: number;
  if (position === 'top') {
    y = boxHeight / 2 + 24;
  } else if (position === 'center') {
    y = canvas.height / 2;
  } else {
    y = canvas.height - boxHeight / 2 - 24;
  }

  context.fillStyle = 'rgba(0, 0, 0, 0.45)';
  context.fillRect(x - boxWidth / 2, y - boxHeight / 2, boxWidth, boxHeight);
  context.fillStyle = color;
  context.fillText(text, x, y);

  return canvasToDataUrl(canvas);
}
