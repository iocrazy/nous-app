"""
Storyboard Export Service

Provides three export formats for Storyboard Workbench projects:
- PNG  : merged frame grid (via StoryboardImageService)
- PDF  : per-frame pages with metadata tables, built from Pillow images
- ZIP  : full project archive (JSON data + all media files)

NAS layout assumed:
    <NAS_BASE_PATH>/teams/<team_id>/storyboard/<project_id>/
        frames/
        thumbnails/
        characters/
        videos/
        exports/

Export filenames:  {project_name}_{timestamp}_{format}.{ext}
Timestamp format:  %Y%m%d_%H%M%S
"""

import asyncio
import json
import logging
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

from app.repositories.storyboard_repository import (
    StoryboardAssetRepository,
    StoryboardCharacterRepository,
    StoryboardFrameRepository,
    StoryboardNodeRepository,
    StoryboardProjectRepository,
)
from app.services.storyboard_image_service import StoryboardImageService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NAS_BASE_PATH = os.environ.get("NAS_BASE_PATH", "/app/downloads")

_PLACEHOLDER_COLOR = (40, 40, 40)  # dark grey for missing-frame cells
_PDF_PAGE_W = 1240  # A4-ish at 150 dpi
_PDF_PAGE_H = 1754
_PDF_MARGIN = 60
_PDF_FRAME_H = 700  # height reserved for the frame image
_METADATA_ROW_H = 36
_METADATA_LABEL_W = 220
_METADATA_VALUE_W = _PDF_PAGE_W - 2 * _PDF_MARGIN - _METADATA_LABEL_W
_CHAR_CARD_W = 200
_CHAR_CARD_H = 260
_CHAR_THUMB_H = 180

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
_FONT_BOLD_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_name(name: str) -> str:
    """Strip characters unsafe for filenames, collapse whitespace."""
    cleaned = re.sub(r"[^\w\s\-]", "", name or "project")
    return re.sub(r"\s+", "_", cleaned.strip()) or "project"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _load_font(paths: List[str], size: int) -> ImageFont.FreeTypeFont:
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _project_root(project: Dict[str, Any]) -> Path:
    """Resolve the NAS root directory for a project."""
    team_id = project.get("team_id", "unknown")
    project_id = project.get("id", "unknown")
    return Path(NAS_BASE_PATH) / "teams" / team_id / "storyboard" / project_id


def _exports_dir(project: Dict[str, Any]) -> Path:
    root = _project_root(project)
    exports = root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    return exports


def _placeholder_image(width: int = 640, height: int = 360) -> Image.Image:
    """Return a solid dark-grey placeholder PIL image."""
    img = Image.new("RGB", (width, height), color=_PLACEHOLDER_COLOR)
    draw = ImageDraw.Draw(img)
    font = _load_font(_FONT_PATHS, 20)
    label = "[ no image ]"
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((width - tw) // 2, (height - th) // 2),
        label,
        font=font,
        fill=(120, 120, 120),
    )
    return img


def _open_image_or_placeholder(path: Optional[str]) -> Image.Image:
    """Open an image file; return a grey placeholder on any failure."""
    if path:
        try:
            return Image.open(path).convert("RGB")
        except Exception as exc:
            logger.warning("Cannot open image %s – %s; using placeholder", path, exc)
    return _placeholder_image()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class StoryboardExportService:
    """Generates PNG, PDF, and ZIP exports for a storyboard project."""

    def __init__(self) -> None:
        self.project_repo = StoryboardProjectRepository()
        self.node_repo = StoryboardNodeRepository()
        self.frame_repo = StoryboardFrameRepository()
        self.character_repo = StoryboardCharacterRepository()
        self.asset_repo = StoryboardAssetRepository()
        self.image_service = StoryboardImageService()

    # ------------------------------------------------------------------ #
    # 1. export_png
    # ------------------------------------------------------------------ #

    async def export_png(
        self, project_id: str, options: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Merge all project frames into a single grid PNG and save to exports/.

        Args:
            project_id: UUID of the storyboard project.
            options: Optional dict with keys:
                - include_annotations (bool, default False)
                - include_frame_numbers (bool, default True)
                - cols (int, default 3)

        Returns:
            Absolute path to the saved PNG file.

        Raises:
            ValueError: When the project has no frames.
            RuntimeError: On any I/O failure.
        """
        opts = options or {}
        include_annotations: bool = bool(opts.get("include_annotations", False))
        include_frame_numbers: bool = bool(opts.get("include_frame_numbers", True))
        cols: int = max(1, int(opts.get("cols", 3)))

        project, frames = await asyncio.gather(
            self.project_repo.get_by_id(project_id),
            self.frame_repo.get_by_project(project_id),
        )

        if not project:
            raise ValueError(f"Project {project_id} not found")
        if not frames:
            raise ValueError(f"Project {project_id} has no frames to export")

        # Frames are already ordered by sort_order from the repository
        frame_paths: List[str] = [f.get("image_path", "") or "" for f in frames]
        notes: Optional[List[str]] = None
        if include_annotations:
            notes = [f.get("notes") or "" for f in frames]

        exports = _exports_dir(project)
        project_name = _safe_name(project.get("name", "project"))
        filename = f"{project_name}_{_timestamp()}_grid.png"
        out_path = exports / filename

        try:
            # image_service.merge_frames saves to the first-frame's directory;
            # we copy the result to the exports folder instead.
            merged_path = self.image_service.merge_frames(
                frame_paths=frame_paths,
                cols=cols,
                frame_numbers=include_frame_numbers,
                notes=notes,
            )
            # Move to exports directory with the correct export name
            import shutil

            shutil.move(merged_path, str(out_path))
        except Exception as exc:
            logger.error(
                "export_png: failed to merge frames for project %s – %s",
                project_id,
                exc,
            )
            raise RuntimeError(f"PNG export failed: {exc}") from exc

        logger.info("export_png: saved → %s", out_path)
        return str(out_path)

    # ------------------------------------------------------------------ #
    # 2. export_pdf
    # ------------------------------------------------------------------ #

    async def export_pdf(
        self, project_id: str, options: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Build a Pillow-based PDF where each page covers one storyboard frame.

        Page layout:
          • Page 1 – project cover: title, description, character card strip
          • Subsequent pages – frame image + metadata table + notes

        Args:
            project_id: UUID of the storyboard project.
            options: Reserved for future use.

        Returns:
            Absolute path to the saved PDF file.

        Raises:
            ValueError: When the project is not found.
            RuntimeError: On any I/O failure.
        """
        project, frames, characters = await asyncio.gather(
            self.project_repo.get_by_id(project_id),
            self.frame_repo.get_by_project(project_id),
            self.character_repo.list_by_project(project_id),
        )

        if not project:
            raise ValueError(f"Project {project_id} not found")

        pages: List[Image.Image] = []

        # -- Cover page -------------------------------------------------------
        pages.append(self._build_cover_page(project, characters))

        # -- Frame pages -------------------------------------------------------
        for frame in frames:
            pages.append(self._build_frame_page(frame))

        # -- Save as multi-page PDF using Pillow -------------------------------
        exports = _exports_dir(project)
        project_name = _safe_name(project.get("name", "project"))
        filename = f"{project_name}_{_timestamp()}_storyboard.pdf"
        out_path = exports / filename

        try:
            if not pages:
                raise RuntimeError("No pages generated for PDF export")

            first_page, rest = pages[0], pages[1:]
            first_page.save(
                str(out_path),
                format="PDF",
                save_all=True,
                append_images=rest,
                resolution=150,
            )
        except Exception as exc:
            logger.error(
                "export_pdf: failed to save PDF for project %s – %s",
                project_id,
                exc,
            )
            raise RuntimeError(f"PDF export failed: {exc}") from exc

        logger.info("export_pdf: saved %d pages → %s", len(pages), out_path)
        return str(out_path)

    def _new_page(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        """Create a blank white A4-ish page and its draw context."""
        page = Image.new("RGB", (_PDF_PAGE_W, _PDF_PAGE_H), color=(255, 255, 255))
        draw = ImageDraw.Draw(page)
        return page, draw

    def _build_cover_page(
        self,
        project: Dict[str, Any],
        characters: List[Dict[str, Any]],
    ) -> Image.Image:
        """Render the project cover page with title and character overview."""
        page, draw = self._new_page()
        font_title = _load_font(_FONT_BOLD_PATHS, 52)
        font_body = _load_font(_FONT_PATHS, 28)
        font_label = _load_font(_FONT_BOLD_PATHS, 20)
        font_small = _load_font(_FONT_PATHS, 18)

        y = _PDF_MARGIN

        # Title
        title = project.get("name") or "Untitled Project"
        draw.text((_PDF_MARGIN, y), title, font=font_title, fill=(20, 20, 20))
        bbox = draw.textbbox((_PDF_MARGIN, y), title, font=font_title)
        y = bbox[3] + 20

        # Description
        description = project.get("description") or ""
        if description:
            draw.text((_PDF_MARGIN, y), description, font=font_body, fill=(80, 80, 80))
            desc_bbox = draw.textbbox((_PDF_MARGIN, y), description, font=font_body)
            y = desc_bbox[3] + 40

        # Divider
        draw.line(
            [(_PDF_MARGIN, y), (_PDF_PAGE_W - _PDF_MARGIN, y)],
            fill=(200, 200, 200),
            width=2,
        )
        y += 30

        # Characters section header
        if characters:
            draw.text(
                (_PDF_MARGIN, y),
                "Characters",
                font=font_label,
                fill=(40, 40, 40),
            )
            y += 36

            # Render character cards in a row, wrapping when needed
            x = _PDF_MARGIN
            row_y = y
            for char in characters:
                if x + _CHAR_CARD_W > _PDF_PAGE_W - _PDF_MARGIN:
                    x = _PDF_MARGIN
                    row_y += _CHAR_CARD_H + 20

                # Character image
                ref_img = _open_image_or_placeholder(char.get("reference_image_path"))
                ref_img = ref_img.resize((_CHAR_CARD_W, _CHAR_THUMB_H), Image.LANCZOS)
                page.paste(ref_img, (x, row_y))

                # Character name
                char_name = char.get("name") or "Unknown"
                draw.text(
                    (x, row_y + _CHAR_THUMB_H + 6),
                    char_name,
                    font=font_small,
                    fill=(30, 30, 30),
                )

                x += _CHAR_CARD_W + 20

        return page

    def _build_frame_page(self, frame: Dict[str, Any]) -> Image.Image:
        """Render a single storyboard frame as a PDF page."""
        page, draw = self._new_page()
        font_label = _load_font(_FONT_BOLD_PATHS, 20)
        font_value = _load_font(_FONT_PATHS, 20)
        font_notes = _load_font(_FONT_PATHS, 18)

        m = _PDF_MARGIN

        # Frame image
        frame_img = _open_image_or_placeholder(frame.get("image_path"))
        img_w = _PDF_PAGE_W - 2 * m
        img_h = _PDF_FRAME_H
        frame_img = frame_img.resize((img_w, img_h), Image.LANCZOS)
        page.paste(frame_img, (m, m))

        y = m + img_h + 20

        # Metadata table
        meta_fields: List[tuple[str, str]] = [
            ("Shot Type", frame.get("shot_type") or "—"),
            ("Camera Angle", frame.get("camera_angle") or "—"),
            ("Camera Movement", frame.get("camera_movement") or "—"),
            ("Focal Length", frame.get("focal_length") or "—"),
            ("Lighting", frame.get("lighting") or "—"),
        ]

        for label, value in meta_fields:
            # Alternating row background
            row_bg = (
                (245, 247, 250)
                if meta_fields.index((label, value)) % 2 == 0
                else (255, 255, 255)
            )
            draw.rectangle(
                [m, y, _PDF_PAGE_W - m, y + _METADATA_ROW_H],
                fill=row_bg,
            )
            draw.text((m + 8, y + 8), label, font=font_label, fill=(60, 60, 60))
            draw.text(
                (m + _METADATA_LABEL_W + 8, y + 8),
                str(value),
                font=font_value,
                fill=(30, 30, 30),
            )
            y += _METADATA_ROW_H

        y += 20

        # Notes section
        notes = (frame.get("notes") or "").strip()
        if notes:
            draw.text((m, y), "Notes:", font=font_label, fill=(60, 60, 60))
            y += 28
            draw.text((m, y), notes, font=font_notes, fill=(50, 50, 50))

        return page

    # ------------------------------------------------------------------ #
    # 3. export_zip
    # ------------------------------------------------------------------ #

    async def export_zip(self, project_id: str) -> str:
        """
        Bundle all project data and media files into a single ZIP archive.

        Archive structure:
            project.json          – full structured project data
            frames/*.png          – frame images
            characters/*.png      – character reference images
            videos/*.mp4          – video assets (if any)
            thumbnails/*.jpg      – thumbnails

        Args:
            project_id: UUID of the storyboard project.

        Returns:
            Absolute path to the saved ZIP file.

        Raises:
            ValueError: When the project is not found.
            RuntimeError: On ZIP creation failure.
        """
        project, nodes, frames, characters, assets = await asyncio.gather(
            self.project_repo.get_by_id(project_id),
            self.node_repo.get_by_project(project_id),
            self.frame_repo.get_by_project(project_id),
            self.character_repo.list_by_project(project_id),
            self.asset_repo.list_by_project(project_id),
        )

        if not project:
            raise ValueError(f"Project {project_id} not found")

        # Fetch edges separately (not in the standard gather above)
        try:
            from app.repositories.storyboard_repository import StoryboardEdgeRepository

            edge_repo = StoryboardEdgeRepository()
            edges = await edge_repo.get_by_project(project_id)
        except Exception as exc:
            logger.warning("export_zip: could not fetch edges – %s", exc)
            edges = []

        exports = _exports_dir(project)
        project_name = _safe_name(project.get("name", "project"))
        filename = f"{project_name}_{_timestamp()}_archive.zip"
        out_path = exports / filename

        try:
            with zipfile.ZipFile(str(out_path), "w", zipfile.ZIP_DEFLATED) as zf:
                # -- project.json -------------------------------------------------
                project_data = {
                    "project": project,
                    "nodes": nodes,
                    "edges": edges,
                    "frames": frames,
                    "characters": characters,
                    "assets": assets,
                }
                zf.writestr(
                    "project.json",
                    json.dumps(project_data, indent=2, default=str),
                )

                # -- Frame images -------------------------------------------------
                self._zip_media_list(
                    zf,
                    items=frames,
                    path_field="image_path",
                    arc_prefix="frames",
                    fallback_ext=".png",
                )

                # -- Frame thumbnails ---------------------------------------------
                self._zip_media_list(
                    zf,
                    items=frames,
                    path_field="thumbnail_path",
                    arc_prefix="thumbnails",
                    fallback_ext=".jpg",
                )

                # -- Character reference images ------------------------------------
                self._zip_media_list(
                    zf,
                    items=characters,
                    path_field="reference_image_path",
                    arc_prefix="characters",
                    fallback_ext=".png",
                )

                # -- Video assets -------------------------------------------------
                video_assets = [
                    a
                    for a in assets
                    if (a.get("asset_type") or "").lower() == "video"
                    or (a.get("filename") or "").lower().endswith(".mp4")
                ]
                self._zip_media_list(
                    zf,
                    items=video_assets,
                    path_field="file_path",
                    arc_prefix="videos",
                    fallback_ext=".mp4",
                )

        except Exception as exc:
            logger.error(
                "export_zip: failed to create ZIP for project %s – %s",
                project_id,
                exc,
            )
            raise RuntimeError(f"ZIP export failed: {exc}") from exc

        logger.info("export_zip: saved → %s", out_path)
        return str(out_path)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _zip_media_list(
        zf: zipfile.ZipFile,
        items: List[Dict[str, Any]],
        path_field: str,
        arc_prefix: str,
        fallback_ext: str,
    ) -> None:
        """
        Add media files from a list of dicts into a ZIP subfolder.

        Missing or unreadable files are skipped with a warning.

        Args:
            zf: Open ZipFile to write into.
            items: List of row dicts (frames, characters, assets …).
            path_field: Dict key that holds the filesystem path.
            arc_prefix: Archive directory prefix (e.g. 'frames').
            fallback_ext: File extension used when the path has none.
        """
        seen: set[str] = set()
        for item in items:
            raw_path = item.get(path_field)
            if not raw_path:
                continue
            src = Path(raw_path)
            if not src.exists():
                logger.debug("_zip_media_list: %s not found, skipping", src)
                continue

            arc_name = f"{arc_prefix}/{src.name}"
            # Deduplicate if two items point at the same file
            if arc_name in seen:
                item_id = item.get("id", "")
                suffix = src.suffix or fallback_ext
                arc_name = f"{arc_prefix}/{item_id}{suffix}"
            seen.add(arc_name)

            try:
                zf.write(str(src), arc_name)
            except Exception as exc:
                logger.warning("_zip_media_list: cannot add %s to ZIP – %s", src, exc)
