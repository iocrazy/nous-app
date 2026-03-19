// HTTP implementation of the ImageSplitGateway interface.
// Delegates heavy image processing to the backend, with client-side canvas fallback.

import type { ImageSplitGateway } from '../application/ports';
import {
  splitImage as splitImageApi,
  type SplitImageResult,
} from '../../../services/storyboardService';

/**
 * HttpToolGateway wraps the storyboard backend API for image operations.
 *
 * For split operations, it calls the server-side split-image endpoint.
 * The gateway requires a projectId and assetId — the split endpoint
 * needs to know which project asset to split.
 */
export class HttpToolGateway implements ImageSplitGateway {
  private projectId: string;
  private assetId: string;

  constructor(projectId = '', assetId = '') {
    this.projectId = projectId;
    this.assetId = assetId;
  }

  /**
   * Update the project and asset context for subsequent operations.
   * Call this before invoking split() when the context changes.
   */
  configure(projectId: string, assetId: string): void {
    this.projectId = projectId;
    this.assetId = assetId;
  }

  /**
   * Split an image into a grid of rows x cols using the backend API.
   * Returns an array of image URLs for each resulting frame.
   *
   * @param _imageSource - Ignored when assetId is available (backend uses asset reference)
   * @param rows - Number of grid rows
   * @param cols - Number of grid columns
   * @param _lineThickness - Line thickness hint (handled server-side)
   */
  async split(
    _imageSource: string,
    rows: number,
    cols: number,
    _lineThickness: number,
  ): Promise<string[]> {
    if (!this.projectId || !this.assetId) {
      throw new Error(
        'HttpToolGateway: projectId and assetId must be configured before calling split()',
      );
    }

    const result: SplitImageResult = await splitImageApi(
      this.projectId,
      this.assetId,
      rows,
      cols,
    );

    // Return the image URLs ordered by frame_index (sort_order).
    const sortedFrames = [...result.frames].sort((a, b) => a.sort_order - b.sort_order);
    return sortedFrames.map((frame) => frame.image_url);
  }
}
