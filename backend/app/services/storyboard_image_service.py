"""
Storyboard Image Processing Service

Provides image manipulation utilities for the Storyboard Workbench:
- Split sprite sheets into individual frames
- Detect scene changes in video files via FFmpeg
- Merge frames into a grid layout with optional annotations
- Generate preview thumbnails
- Embed/read PNG metadata chunks
- Compute file hashes

All methods are synchronous (CPU-bound, intended for Celery workers).
"""

import hashlib
import json
import logging
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PREVIEW_QUALITY = 85
FRAME_NUMBER_FONT_SIZE = 16
NOTE_FONT_SIZE = 12
TEXT_PADDING = 4
PNG_METADATA_KEY = "storyboard_metadata"


class StoryboardImageService:
    """Image processing utilities for the Storyboard Workbench module."""

    # ------------------------------------------------------------------ #
    # 1. split_image
    # ------------------------------------------------------------------ #

    def split_image(self, image_path: str, rows: int, cols: int) -> list[str]:
        """
        Split a sprite-sheet image into individual frame files.

        Crops each frame in row-major order (left to right, top to bottom)
        and saves each as ``<stem>_frame_<index>.png`` in the same directory.

        Args:
            image_path: Absolute path to the source image.
            rows: Number of rows in the sprite sheet.
            cols: Number of columns in the sprite sheet.

        Returns:
            Ordered list of absolute paths to the saved frame files.
        """
        src = Path(image_path)
        try:
            img = Image.open(src)
        except Exception as exc:
            logger.error("split_image: cannot open %s – %s", image_path, exc)
            raise

        img_w, img_h = img.size
        frame_w = img_w // cols
        frame_h = img_h // rows

        frame_paths: list[str] = []
        index = 0

        for row in range(rows):
            for col in range(cols):
                left = col * frame_w
                upper = row * frame_h
                right = left + frame_w
                lower = upper + frame_h

                frame = img.crop((left, upper, right, lower))
                frame_path = src.parent / f"{src.stem}_frame_{index}.png"
                frame.save(str(frame_path), format="PNG")
                frame_paths.append(str(frame_path))
                index += 1

        logger.info(
            "split_image: split %s into %d frames (%d rows × %d cols)",
            image_path,
            len(frame_paths),
            rows,
            cols,
        )
        return frame_paths

    # ------------------------------------------------------------------ #
    # 1b. split_image_to_grid (rich output with previews)
    # ------------------------------------------------------------------ #

    def split_image_to_grid(
        self,
        image_path: str,
        rows: int,
        cols: int,
        output_dir: str,
        preview_dir: str,
        file_prefix: str = "cell",
        preview_max_size: int = 512,
    ) -> list[dict]:
        """
        Split a sprite-sheet image into a grid of cells with preview thumbnails.

        Each cell is saved as a PNG in *output_dir* and a JPEG preview in
        *preview_dir*.  Returns rich metadata for each cell including
        dimensions, row/col position, and file paths.

        Args:
            image_path: Absolute path to the source image.
            rows: Number of rows in the grid.
            cols: Number of columns in the grid.
            output_dir: Directory to save full-resolution cell PNGs.
            preview_dir: Directory to save preview JPEGs.
            file_prefix: Filename prefix for saved cells.
            preview_max_size: Max pixel dimension for preview thumbnails.

        Returns:
            List of dicts with keys: file_path, preview_path, width, height,
            row, col, index.
        """
        src = Path(image_path)
        out = Path(output_dir)
        prev = Path(preview_dir)
        out.mkdir(parents=True, exist_ok=True)
        prev.mkdir(parents=True, exist_ok=True)

        try:
            img = Image.open(src).convert("RGB")
        except Exception as exc:
            logger.error("split_image_to_grid: cannot open %s – %s", image_path, exc)
            raise

        img_w, img_h = img.size
        cell_w = img_w // cols
        cell_h = img_h // rows

        if cell_w < 1 or cell_h < 1:
            raise ValueError(f"Image {img_w}x{img_h} too small for {rows}x{cols} grid")

        results: list[dict] = []
        index = 0

        for row in range(rows):
            for col in range(cols):
                left = col * cell_w
                upper = row * cell_h
                right = left + cell_w
                lower = upper + cell_h

                cell = img.crop((left, upper, right, lower))

                # Save full-resolution cell
                cell_filename = f"{file_prefix}_{row}_{col}.png"
                cell_path = out / cell_filename
                cell.save(str(cell_path), format="PNG")

                # Generate preview thumbnail
                preview_filename = f"{file_prefix}_{row}_{col}_thumb.jpg"
                preview_path = prev / preview_filename
                scale = min(
                    preview_max_size / max(cell_w, 1),
                    preview_max_size / max(cell_h, 1),
                    1.0,
                )
                if scale < 1.0:
                    thumb = cell.resize(
                        (max(1, int(cell_w * scale)), max(1, int(cell_h * scale))),
                        Image.LANCZOS,
                    )
                else:
                    thumb = cell
                thumb.save(str(preview_path), format="JPEG", quality=PREVIEW_QUALITY)

                results.append(
                    {
                        "file_path": str(cell_path),
                        "preview_path": str(preview_path),
                        "width": cell_w,
                        "height": cell_h,
                        "row": row,
                        "col": col,
                        "index": index,
                    }
                )
                index += 1

        logger.info(
            "split_image_to_grid: split %s into %d cells (%d×%d, cell %dx%d)",
            image_path,
            len(results),
            rows,
            cols,
            cell_w,
            cell_h,
        )
        return results

    # ------------------------------------------------------------------ #
    # 2. detect_scenes
    # ------------------------------------------------------------------ #

    def detect_scenes(self, video_path: str, threshold: float = 30.0) -> list[dict]:
        """
        Detect scene changes in a video file using FFmpeg + pixel diffing.

        Extracts one frame per second via FFmpeg, then compares consecutive
        frames by the percentage of pixels whose brightness changed by more
        than a fixed amount.  A frame is declared a scene change when that
        percentage exceeds *threshold*.

        The first and last frames are always included in the result.

        Args:
            video_path: Absolute path to the video file.
            threshold: Pixel-change percentage (0–100) that triggers a scene
                       change detection.  Defaults to 30.0.

        Returns:
            List of dicts with keys ``time`` (float, seconds from start) and
            ``image_path`` (str, absolute path to the saved frame PNG).
        """
        try:
            import numpy as np
        except ImportError:
            logger.error("detect_scenes: numpy is required but not installed")
            raise

        src = Path(video_path)
        with tempfile.TemporaryDirectory(prefix="sb_scenes_") as tmp_dir:
            tmp = Path(tmp_dir)

            # Extract one frame per second as PNG files
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-vf",
                "fps=1",
                str(tmp / "frame_%06d.png"),
            ]
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if result.returncode != 0:
                err = result.stderr.decode(errors="replace")[:500]
                logger.error(
                    "detect_scenes: ffmpeg failed for %s – %s", video_path, err
                )
                raise RuntimeError(f"FFmpeg failed: {err}")

            frame_files = sorted(tmp.glob("frame_*.png"))
            if not frame_files:
                logger.warning("detect_scenes: no frames extracted from %s", video_path)
                return []

            scenes: list[dict] = []
            prev_array = None
            out_dir = src.parent

            for idx, frame_file in enumerate(frame_files):
                frame_img = Image.open(frame_file).convert("L")  # grayscale
                curr_array = np.array(frame_img, dtype=np.int16)

                is_scene = False
                if prev_array is None:
                    # Always include the first frame
                    is_scene = True
                else:
                    diff = np.abs(curr_array - prev_array)
                    changed_pct = (diff > 25).sum() / diff.size * 100.0
                    if changed_pct > threshold:
                        is_scene = True

                # Always include the last frame
                if idx == len(frame_files) - 1:
                    is_scene = True

                if is_scene:
                    timestamp = float(idx)
                    out_path = out_dir / f"{src.stem}_scene_{idx:06d}.png"
                    frame_img_color = Image.open(frame_file).convert("RGB")
                    frame_img_color.save(str(out_path), format="PNG")
                    scenes.append({"time": timestamp, "image_path": str(out_path)})

                prev_array = curr_array

        logger.info(
            "detect_scenes: found %d scene(s) in %s (threshold=%.1f%%)",
            len(scenes),
            video_path,
            threshold,
        )
        return scenes

    # ------------------------------------------------------------------ #
    # 3. merge_frames
    # ------------------------------------------------------------------ #

    def merge_frames(
        self,
        frame_paths: list[str],
        cols: int = 3,
        frame_numbers: bool = True,
        notes: list[str] | None = None,
    ) -> str:
        """
        Arrange frames in a grid and save the merged image.

        All frames are resized to match the first frame's dimensions.
        If the total number of frames is not divisible by *cols*, blank
        (black) padding cells are added.  Optional frame-number labels and
        note text are drawn on top of each cell.

        The merged image is saved as ``<first_stem>_merged.png`` in the same
        directory as the first frame.

        Args:
            frame_paths: Ordered list of absolute paths to frame images.
            cols: Number of columns in the output grid.  Defaults to 3.
            frame_numbers: When True, overlay a 1-based frame number in the
                           top-left corner of every cell.
            notes: Optional list of per-frame note strings.  When provided,
                   each note is overlaid at the bottom of its cell.

        Returns:
            Absolute path to the saved merged image.
        """
        if not frame_paths:
            raise ValueError("merge_frames: frame_paths must not be empty")

        first_path = Path(frame_paths[0])
        first_img = Image.open(str(first_path)).convert("RGB")
        cell_w, cell_h = first_img.size

        total = len(frame_paths)
        rows = (total + cols - 1) // cols  # ceiling division

        canvas_w = cols * cell_w
        canvas_h = rows * cell_h
        canvas = Image.new("RGB", (canvas_w, canvas_h), color=(0, 0, 0))

        try:
            font_number = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                FRAME_NUMBER_FONT_SIZE,
            )
            font_note = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                NOTE_FONT_SIZE,
            )
        except (IOError, OSError):
            # Fall back to default bitmap font when system fonts are unavailable
            font_number = ImageFont.load_default()
            font_note = ImageFont.load_default()

        for idx, fp in enumerate(frame_paths):
            row = idx // cols
            col = idx % cols
            x_off = col * cell_w
            y_off = row * cell_h

            try:
                cell_img = Image.open(fp).convert("RGB")
                if cell_img.size != (cell_w, cell_h):
                    cell_img = cell_img.resize((cell_w, cell_h), Image.LANCZOS)
            except Exception as exc:
                logger.warning(
                    "merge_frames: cannot open frame %s – %s; using blank", fp, exc
                )
                cell_img = Image.new("RGB", (cell_w, cell_h), color=(0, 0, 0))

            canvas.paste(cell_img, (x_off, y_off))
            draw = ImageDraw.Draw(canvas)

            if frame_numbers:
                label = str(idx + 1)
                tx = x_off + TEXT_PADDING
                ty = y_off + TEXT_PADDING
                # Black outline
                for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                    draw.text(
                        (tx + dx, ty + dy), label, font=font_number, fill=(0, 0, 0)
                    )
                draw.text((tx, ty), label, font=font_number, fill=(255, 255, 255))

            if notes and idx < len(notes) and notes[idx]:
                note_text = notes[idx]
                bbox = draw.textbbox((0, 0), note_text, font=font_note)
                text_h = bbox[3] - bbox[1]
                tx = x_off + TEXT_PADDING
                ty = y_off + cell_h - text_h - TEXT_PADDING
                for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                    draw.text(
                        (tx + dx, ty + dy), note_text, font=font_note, fill=(0, 0, 0)
                    )
                draw.text((tx, ty), note_text, font=font_note, fill=(255, 255, 255))

        out_path = first_path.parent / f"{first_path.stem}_merged.png"
        canvas.save(str(out_path), format="PNG")
        logger.info("merge_frames: saved %d frames → %s", total, out_path)
        return str(out_path)

    # ------------------------------------------------------------------ #
    # 4. generate_preview
    # ------------------------------------------------------------------ #

    def generate_preview(self, image_path: str, max_size: int = 512) -> str:
        """
        Generate a JPEG thumbnail with the longest side capped at *max_size*.

        The thumbnail is saved as ``<stem>_thumb.jpg`` in the same directory
        as the source image.

        Args:
            image_path: Absolute path to the source image.
            max_size: Maximum pixel length for the longest side.  Defaults to 512.

        Returns:
            Absolute path to the saved thumbnail JPEG.
        """
        src = Path(image_path)
        try:
            img = Image.open(src).convert("RGB")
        except Exception as exc:
            logger.error("generate_preview: cannot open %s – %s", image_path, exc)
            raise

        orig_w, orig_h = img.size
        scale = min(max_size / orig_w, max_size / orig_h, 1.0)

        if scale < 1.0:
            new_w = max(1, int(orig_w * scale))
            new_h = max(1, int(orig_h * scale))
            img = img.resize((new_w, new_h), Image.LANCZOS)

        out_path = src.parent / f"{src.stem}_thumb.jpg"
        img.save(str(out_path), format="JPEG", quality=PREVIEW_QUALITY)
        logger.info("generate_preview: saved preview → %s", out_path)
        return str(out_path)

    # ------------------------------------------------------------------ #
    # 5. embed_png_metadata
    # ------------------------------------------------------------------ #

    def embed_png_metadata(self, image_path: str, metadata: dict) -> None:
        """
        Embed a JSON-serialised metadata dict in a PNG text chunk.

        The chunk key is ``storyboard_metadata``.  The file is overwritten
        in place.

        Args:
            image_path: Absolute path to the PNG file.
            metadata: Arbitrary dict to serialise and embed.
        """
        src = Path(image_path)
        try:
            img = Image.open(src)
        except Exception as exc:
            logger.error("embed_png_metadata: cannot open %s – %s", image_path, exc)
            raise

        png_info = PngImagePlugin.PngInfo()
        png_info.add_text(PNG_METADATA_KEY, json.dumps(metadata))
        img.save(str(src), format="PNG", pnginfo=png_info)
        logger.debug("embed_png_metadata: wrote metadata to %s", image_path)

    # ------------------------------------------------------------------ #
    # 6. read_png_metadata
    # ------------------------------------------------------------------ #

    def read_png_metadata(self, image_path: str) -> dict | None:
        """
        Read the ``storyboard_metadata`` text chunk from a PNG file.

        Args:
            image_path: Absolute path to the PNG file.

        Returns:
            Parsed metadata dict, or ``None`` if the chunk is absent or
            the JSON cannot be parsed.
        """
        src = Path(image_path)
        try:
            img = Image.open(src)
        except Exception as exc:
            logger.error("read_png_metadata: cannot open %s – %s", image_path, exc)
            raise

        raw = img.info.get(PNG_METADATA_KEY)
        if raw is None:
            logger.debug("read_png_metadata: no metadata chunk in %s", image_path)
            return None

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "read_png_metadata: invalid JSON in %s – %s", image_path, exc
            )
            return None

    # ------------------------------------------------------------------ #
    # 7. compute_file_hash
    # ------------------------------------------------------------------ #

    def compute_file_hash(self, file_path: str) -> str:
        """
        Compute the SHA-256 hash of a file's contents.

        Reads the file in 64 KiB chunks to avoid loading large files fully
        into memory.

        Args:
            file_path: Absolute path to the file.

        Returns:
            Lowercase hex digest of the SHA-256 hash.
        """
        src = Path(file_path)
        hasher = hashlib.sha256()
        try:
            with src.open("rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    hasher.update(chunk)
        except OSError as exc:
            logger.error("compute_file_hash: cannot read %s – %s", file_path, exc)
            raise

        digest = hasher.hexdigest()
        logger.debug("compute_file_hash: %s → %s", file_path, digest)
        return digest
