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

interface ImagePipelineError extends Error {
  details?: string;
}

// ─── Error helpers ───────────────────────────────────────────────────────────

function stringifyUnknown(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value instanceof Error) return value.message;
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function createImagePipelineError(message: string, details?: string, cause?: unknown): ImagePipelineError {
  const error: ImagePipelineError = new Error(message);
  const parts: string[] = [];
  if (details) parts.push(details);
  if (cause !== undefined) parts.push(`cause: ${stringifyUnknown(cause)}`);
  if (parts.length > 0) error.details = parts.join('\n');
  return error;
}

// ─── Zoom threshold ──────────────────────────────────────────────────────────

const ORIGINAL_IMAGE_ZOOM_THRESHOLD = 1.45;

export function shouldUseOriginalImageByZoom(zoom: number): boolean {
  return Number.isFinite(zoom) && zoom >= ORIGINAL_IMAGE_ZOOM_THRESHOLD;
}

// ─── Image element cache ─────────────────────────────────────────────────────

const imageElementCache = new Map<string, HTMLImageElement>();
const IMAGE_CACHE_MAX_SIZE = 64;

function evictImageCache(): void {
  if (imageElementCache.size <= IMAGE_CACHE_MAX_SIZE) return;
  const keysToDelete = Array.from(imageElementCache.keys()).slice(0, imageElementCache.size - IMAGE_CACHE_MAX_SIZE);
  for (const key of keysToDelete) {
    imageElementCache.delete(key);
  }
}

/**
 * Get a cached HTMLImageElement or null if not yet cached.
 */
export function getCachedImageElement(source: string): HTMLImageElement | null {
  return imageElementCache.get(source) ?? null;
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

const DEFAULT_LOAD_RETRY_COUNT = 2;
const RETRY_DELAY_MS = 500;

/**
 * Load an HTMLImageElement from a source URL.
 * Sets crossOrigin for http(s) sources to allow canvas operations.
 * Uses an internal cache to avoid redundant loads.
 * Retries on failure for http(s) sources.
 */
export async function loadImageElement(
  source: string,
  retryCount = DEFAULT_LOAD_RETRY_COUNT
): Promise<HTMLImageElement> {
  const cached = imageElementCache.get(source);
  if (cached) return cached;

  let lastError: Error | null = null;
  const isRemote = source.startsWith('http://') || source.startsWith('https://');
  const maxAttempts = isRemote ? retryCount + 1 : 1;

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    if (attempt > 0) {
      await new Promise<void>((resolve) => {
        setTimeout(resolve, RETRY_DELAY_MS * attempt);
      });
    }

    try {
      const image = await new Promise<HTMLImageElement>((resolve, reject) => {
        const img = new Image();
        if (isRemote) {
          img.crossOrigin = 'anonymous';
        }
        img.onload = () => resolve(img);
        img.onerror = () =>
          reject(createImagePipelineError(
            'Failed to load image',
            `source=${source.slice(0, 120)}, attempt=${attempt + 1}/${maxAttempts}`
          ));
        img.src = source;
      });

      imageElementCache.set(source, image);
      evictImageCache();
      return image;
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));
    }
  }

  throw lastError ?? new Error(`Failed to load image: ${source.slice(0, 120)}`);
}

/**
 * Convert any image URL (http, blob, data) to a data URL via fetch + FileReader.
 * If the source is already a data URL, returns it as-is.
 */
export async function imageUrlToDataUrl(imageUrl: string): Promise<string> {
  if (imageUrl.startsWith('data:')) {
    return imageUrl;
  }

  const response = await fetch(imageUrl);
  if (!response.ok) {
    throw createImagePipelineError(
      'Failed to fetch image',
      `url=${imageUrl.slice(0, 120)}, status=${response.status}`
    );
  }

  const blob = await response.blob();
  return await blobToDataUrl(blob);
}

/**
 * Convert an image URL to a Blob object.
 * Useful for clipboard operations and file downloads.
 */
export async function imageUrlToBlob(imageUrl: string): Promise<Blob> {
  if (imageUrl.startsWith('data:')) {
    const response = await fetch(imageUrl);
    return await response.blob();
  }

  const response = await fetch(imageUrl);
  if (!response.ok) {
    throw createImagePipelineError(
      'Failed to fetch image as blob',
      `url=${imageUrl.slice(0, 120)}, status=${response.status}`
    );
  }

  return await response.blob();
}

// ─── Preview rendering ───────────────────────────────────────────────────────

const DEFAULT_PREVIEW_MAX_DIMENSION = 512;

function resolvePreviewMimeType(imageUrl: string): string {
  if (imageUrl.startsWith('data:image/png')) return 'image/png';
  if (imageUrl.startsWith('data:image/webp')) return 'image/webp';
  return 'image/jpeg';
}

/**
 * Create a downscaled preview data URL from a full-size image.
 */
export async function createPreviewDataUrl(
  imageUrl: string,
  maxDimension = DEFAULT_PREVIEW_MAX_DIMENSION
): Promise<string> {
  const dataUrl = await imageUrlToDataUrl(imageUrl);
  const image = await loadImageElement(dataUrl, 0);
  const longestSide = Math.max(image.naturalWidth, image.naturalHeight);
  if (longestSide <= maxDimension) return dataUrl;

  const scale = maxDimension / longestSide;
  const targetWidth = Math.max(1, Math.round(image.naturalWidth * scale));
  const targetHeight = Math.max(1, Math.round(image.naturalHeight * scale));
  const canvas = document.createElement('canvas');
  canvas.width = targetWidth;
  canvas.height = targetHeight;

  const context = canvas.getContext('2d');
  if (!context) return dataUrl;

  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = 'high';
  context.drawImage(image, 0, 0, targetWidth, targetHeight);

  const mimeType = resolvePreviewMimeType(dataUrl);
  return mimeType === 'image/jpeg' ? canvas.toDataURL(mimeType, 0.86) : canvas.toDataURL(mimeType);
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

/**
 * Clear the image element cache (useful on project switch).
 */
export function clearImageCache(): void {
  imageElementCache.clear();
}
