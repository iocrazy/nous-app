// Migrated from Storyboard-Copilot — Tauri-specific code removed.
// Only pure utility functions are kept.

export function parseAspectRatio(value: string): number {
  const [width, height] = value.split(':').map((item) => Number(item));
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return 1;
  }

  return width / height;
}

export function reduceAspectRatio(width: number, height: number): string {
  if (width <= 0 || height <= 0) {
    return '1:1';
  }

  const gcd = greatestCommonDivisor(Math.round(width), Math.round(height));
  return `${Math.round(width / gcd)}:${Math.round(height / gcd)}`;
}

function greatestCommonDivisor(a: number, b: number): number {
  let x = Math.abs(a);
  let y = Math.abs(b);

  while (y !== 0) {
    const temp = y;
    y = x % y;
    x = temp;
  }

  return x || 1;
}

export interface PreparedNodeImage {
  imageUrl: string;
  previewImageUrl: string;
  aspectRatio: string;
}

export function shouldUseOriginalImageByZoom(zoom: number): boolean {
  const ORIGINAL_IMAGE_ZOOM_THRESHOLD = 1.45;
  return Number.isFinite(zoom) && zoom >= ORIGINAL_IMAGE_ZOOM_THRESHOLD;
}

export async function readFileAsDataUrl(file: File): Promise<string> {
  const reader = new FileReader();

  return await new Promise((resolve, reject) => {
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(new Error('Failed to read file'));
    reader.readAsDataURL(file);
  });
}

export async function blobToDataUrl(blob: Blob): Promise<string> {
  const reader = new FileReader();

  return await new Promise((resolve, reject) => {
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(new Error('Failed to convert blob'));
    reader.readAsDataURL(blob);
  });
}

export function extractBase64Payload(dataUrl: string): string {
  const [, payload = ''] = dataUrl.split(',');
  return payload;
}

export function canvasToDataUrl(canvas: HTMLCanvasElement): string {
  return canvas.toDataURL('image/png');
}

export async function detectAspectRatio(imageUrl: string): Promise<string> {
  const image = new Image();

  return await new Promise((resolve, reject) => {
    image.onload = () => resolve(reduceAspectRatio(image.naturalWidth, image.naturalHeight));
    image.onerror = () => reject(new Error('Failed to load image'));
    if (imageUrl.startsWith('http://') || imageUrl.startsWith('https://')) {
      image.crossOrigin = 'anonymous';
    }
    image.src = imageUrl;
  });
}

// TODO: migrate — prepareNodeImage, prepareNodeImageFromFile, persistImageLocally
// These require backend integration to replace Tauri commands.
