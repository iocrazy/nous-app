// Image data utilities for web-based storyboard canvas.
// Replaces Tauri-specific image pipeline with HTML Canvas + backend upload.

import {
  uploadImage,
  type UploadImageResult,
} from '../../../services/storyboardService';

// ─── Pure math utilities ─────────────────────────────────────────────────────

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

// ─── Types ───────────────────────────────────────────────────────────────────

export interface PreparedNodeImage {
  imageUrl: string;
  previewImageUrl: string;
  aspectRatio: string;
}

export interface PreparedNodeImageWithUpload extends PreparedNodeImage {
  /** Server-side asset ID, available after backend upload completes. */
  assetId?: string;
}

// ─── Zoom threshold ──────────────────────────────────────────────────────────

export function shouldUseOriginalImageByZoom(zoom: number): boolean {
  const ORIGINAL_IMAGE_ZOOM_THRESHOLD = 1.45;
  return Number.isFinite(zoom) && zoom >= ORIGINAL_IMAGE_ZOOM_THRESHOLD;
}

// ─── File / Blob reading ─────────────────────────────────────────────────────

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

// ─── Canvas helpers ──────────────────────────────────────────────────────────

export function canvasToDataUrl(canvas: HTMLCanvasElement): string {
  return canvas.toDataURL('image/png');
}

// ─── Image loading ───────────────────────────────────────────────────────────

/**
 * Load an HTMLImageElement from a source URL.
 * Sets crossOrigin for http(s) sources to allow canvas operations.
 */
export function loadImageElement(source: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    if (source.startsWith('http://') || source.startsWith('https://')) {
      image.crossOrigin = 'anonymous';
    }
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`Failed to load image: ${source.slice(0, 120)}`));
    image.src = source;
  });
}

/**
 * Convert any image URL (http, blob, data) to a data URL via canvas fetch + FileReader.
 * If the source is already a data URL, returns it as-is.
 */
export async function imageUrlToDataUrl(imageUrl: string): Promise<string> {
  if (imageUrl.startsWith('data:')) {
    return imageUrl;
  }

  const response = await fetch(imageUrl);
  if (!response.ok) {
    throw new Error(`Failed to fetch image: ${response.status} ${imageUrl.slice(0, 120)}`);
  }

  const blob = await response.blob();
  return await blobToDataUrl(blob);
}

// ─── Aspect ratio detection ──────────────────────────────────────────────────

/**
 * Detect the aspect ratio of an image by loading it and reading natural dimensions.
 */
export async function detectAspectRatio(imageUrl: string): Promise<string> {
  const image = await loadImageElement(imageUrl);
  return reduceAspectRatio(image.naturalWidth, image.naturalHeight);
}

/**
 * Detect aspect ratio from known width/height without loading an image.
 */
export function detectAspectRatioFromDimensions(width: number, height: number): string {
  return reduceAspectRatio(width, height);
}

// ─── Display URL resolution ──────────────────────────────────────────────────

/**
 * In web context, image URLs are used directly — no Tauri convertFileSrc needed.
 */
export function resolveImageDisplayUrl(imageUrl: string): string {
  return imageUrl;
}

// ─── Node image preparation ──────────────────────────────────────────────────

/**
 * Prepare a node image from a File.
 *
 * 1. Creates a blob URL immediately for optimistic preview.
 * 2. Detects aspect ratio from the blob.
 * 3. If a projectId is provided, uploads the file to the backend in the background
 *    and updates the returned URLs with server-persisted URLs.
 */
export async function prepareNodeImageFromFile(
  file: File,
  projectId?: string,
  nodeId?: string,
): Promise<PreparedNodeImageWithUpload> {
  const blobUrl = URL.createObjectURL(file);
  const aspectRatio = await detectAspectRatio(blobUrl);

  // Without a project context, return blob URLs only (local preview).
  if (!projectId) {
    return {
      imageUrl: blobUrl,
      previewImageUrl: blobUrl,
      aspectRatio,
    };
  }

  // Upload to backend for persistent storage.
  let uploadResult: UploadImageResult;
  try {
    uploadResult = await uploadImage(projectId, file, nodeId);
  } catch (error) {
    // Upload failed — fall back to blob URL so the user still sees the image.
    console.error('[imageData] Backend upload failed, using blob URL fallback', error);
    return {
      imageUrl: blobUrl,
      previewImageUrl: blobUrl,
      aspectRatio,
    };
  }

  // Revoke the temporary blob URL now that we have server URLs.
  URL.revokeObjectURL(blobUrl);

  const serverAspectRatio =
    uploadResult.width > 0 && uploadResult.height > 0
      ? reduceAspectRatio(uploadResult.width, uploadResult.height)
      : aspectRatio;

  return {
    imageUrl: uploadResult.image_url,
    previewImageUrl: uploadResult.preview_url || uploadResult.image_url,
    aspectRatio: serverAspectRatio,
    assetId: uploadResult.asset_id,
  };
}

/**
 * Prepare a node image from an existing URL — detects aspect ratio.
 */
export async function prepareNodeImage(imageUrl: string): Promise<PreparedNodeImage> {
  const aspectRatio = await detectAspectRatio(imageUrl).catch(() => '1:1');

  return {
    imageUrl,
    previewImageUrl: imageUrl,
    aspectRatio,
  };
}

/**
 * Persist image locally — in web context, this is a no-op that returns the same URL.
 * Server-side persistence happens via uploadImage in prepareNodeImageFromFile.
 */
export async function persistImageLocally(dataUrl: string): Promise<string> {
  return dataUrl;
}
