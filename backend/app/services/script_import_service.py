"""Script import service — parse uploaded files and split into chapters via AI."""

import io
from typing import List, Dict

import pypdf
import docx
from loguru import logger

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_PDF_PAGES = 200
MAX_CHAR_COUNT = 500_000

ALLOWED_MIME_TYPES = {
    "text/plain",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class ScriptImportService:
    """Parse uploaded files and split into chapters."""

    def validate_file(
        self, file_bytes: bytes, filename: str, content_type: str
    ) -> None:
        """Validate file size and MIME type. Raises ValueError on failure."""
        if len(file_bytes) > MAX_FILE_SIZE:
            raise ValueError(
                f"File too large: {len(file_bytes)} bytes (max {MAX_FILE_SIZE})"
            )
        if content_type not in ALLOWED_MIME_TYPES:
            raise ValueError(f"Unsupported file type: {content_type}")

    def parse_file(
        self, file_bytes: bytes, filename: str, content_type: str
    ) -> str:
        """Parse file content to plain text. Raises ValueError on failure."""
        if content_type == "text/plain":
            return file_bytes.decode("utf-8", errors="replace")
        if content_type == "application/pdf":
            return self._parse_pdf(file_bytes)
        if (
            content_type
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):
            return self._parse_docx(file_bytes)
        raise ValueError(f"Unsupported content type: {content_type}")

    def _parse_pdf(self, file_bytes: bytes) -> str:
        """Extract text from all pages of a PDF."""
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(
                f"PDF too long: {len(reader.pages)} pages (max {MAX_PDF_PAGES})"
            )
        texts: List[str] = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                texts.append(text)
        result = "\n".join(texts)
        if len(result) > MAX_CHAR_COUNT:
            raise ValueError(
                f"Content too long: {len(result)} chars (max {MAX_CHAR_COUNT})"
            )
        logger.debug(f"Parsed PDF: {len(reader.pages)} pages, {len(result)} chars")
        return result

    def _parse_docx(self, file_bytes: bytes) -> str:
        """Extract text from all paragraphs of a Word document."""
        doc = docx.Document(io.BytesIO(file_bytes))
        texts = [p.text for p in doc.paragraphs if p.text.strip()]
        result = "\n".join(texts)
        if len(result) > MAX_CHAR_COUNT:
            raise ValueError(
                f"Content too long: {len(result)} chars (max {MAX_CHAR_COUNT})"
            )
        logger.debug(f"Parsed DOCX: {len(texts)} paragraphs, {len(result)} chars")
        return result

    def build_single_chapter(self, text: str, filename: str) -> List[Dict]:
        """Wrap raw text as a single imported chapter (fallback when no AI)."""
        name = filename.rsplit(".", 1)[0] if "." in filename else filename
        return [
            {
                "title": name or "Imported Content",
                "summary": text[:300].replace("\n", " "),
                "content": text,
                "content_html": "<p>" + "</p><p>".join(
                    line for line in text.splitlines() if line.strip()
                ) + "</p>",
            }
        ]
