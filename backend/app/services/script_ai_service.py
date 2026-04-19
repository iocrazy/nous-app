"""Script AI Service — LLM-powered outline, expansion, and branching."""

import json
from typing import Any, Dict, List, Optional

import bleach
import httpx
from loguru import logger

from app.core.config import settings

ALLOWED_HTML_TAGS = ["h2", "h3", "p", "strong", "em", "hr", "br"]


def sanitize_ai_html(html: str) -> str:
    """Sanitize AI-generated HTML, only allow script-safe tags."""
    return bleach.clean(html, tags=ALLOWED_HTML_TAGS, strip=True)

# LLM generation defaults
DEFAULT_OUTLINE_TEMPERATURE = 0.7
DEFAULT_EXPAND_TEMPERATURE = 0.8
DEFAULT_BRANCH_TEMPERATURE = 0.9
DEFAULT_MAX_TOKENS = 4096

# Output safety limits
MAX_TITLE_LENGTH = 200
MAX_SUMMARY_LENGTH = 5000
MAX_CONTENT_LENGTH = 50000
MAX_BRANCH_LABEL_LENGTH = 100


class ScriptAIService:
    """AI operations for the script editor module."""

    def __init__(self) -> None:
        self.api_url = settings.LLM_API_URL
        self.api_key = settings.LLM_API_KEY
        self.model = settings.LLM_MODEL
        self.timeout = settings.LLM_TIMEOUT_SECONDS

    async def _call_llm(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.api_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    def _extract_json(self, text: str) -> Any:
        """Extract JSON from LLM response that may be wrapped in markdown fences."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            start = 1
            end = len(lines) - 1
            if lines[-1].strip() == "```":
                cleaned = "\n".join(lines[start:end])
            else:
                cleaned = "\n".join(lines[start:])
        return json.loads(cleaned)

    async def generate_outline(
        self,
        premise: str,
        chapter_count: int = 5,
        style_guide: Optional[str] = None,
        genre: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Generate a story outline with chapter summaries from a premise."""
        system_prompt = (
            "You are a professional screenwriter and story architect. "
            "Generate a story outline as a JSON array of chapter objects.\n\n"
            "Each chapter object must have:\n"
            '- "title": string (chapter title)\n'
            '- "summary": string (2-3 sentence plot summary)\n\n'
            f"Generate exactly {chapter_count} chapters.\n"
            "Return ONLY a JSON array, no other text."
        )
        user_prompt = f"Story premise:\n{premise}"
        if genre:
            user_prompt += f"\n\n故事风格为{genre}，请围绕该风格创作。"
        if style_guide:
            user_prompt += f"\n\nStyle guide:\n{style_guide}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await self._call_llm(
            messages,
            temperature=DEFAULT_OUTLINE_TEMPERATURE,
            max_tokens=DEFAULT_MAX_TOKENS,
        )
        chapters = self._extract_json(response)

        if not isinstance(chapters, list):
            raise ValueError("LLM did not return a JSON array")

        sanitized: List[Dict[str, str]] = []
        for i, ch in enumerate(chapters):
            if not isinstance(ch, dict):
                continue
            # Ensure values are strings, not nested structures
            title = ch.get("title", f"Chapter {i + 1}")
            if not isinstance(title, str):
                title = str(title)[:MAX_TITLE_LENGTH]
            else:
                title = title[:MAX_TITLE_LENGTH]
            summary = ch.get("summary", "")
            if not isinstance(summary, str):
                summary = str(summary)[:MAX_SUMMARY_LENGTH]
            else:
                summary = summary[:MAX_SUMMARY_LENGTH]
            sanitized.append({"title": title, "summary": summary})
        return sanitized

    async def expand_chapter(
        self,
        title: str,
        summary: str,
        context: Optional[str] = None,
        expansion_request: Optional[str] = None,
    ) -> str:
        """Expand a chapter summary into full screenplay HTML content."""
        system_prompt = """You are a professional screenplay writer. Expand the given chapter summary into full screenplay content.

OUTPUT FORMAT (mandatory):
- Scene headings: <h2>场景N：场景名 – 时间 – 内/外景</h2>
- Action/description: <p>paragraph text</p>
- Character dialogue: <p><strong>角色名</strong>：（动作描述）台词内容</p>
- Scene separator: <hr>
- Do NOT wrap output in any container tags. Output raw HTML fragments only.
- Do NOT output markdown. Only HTML tags listed above."""

        user_prompt = f"Chapter title: {title}\nSummary: {summary}"
        if context:
            user_prompt = f"Story context:\n{context}\n\n{user_prompt}"
        if expansion_request:
            user_prompt += f"\n\nAdditional requirements: {expansion_request}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        content = await self._call_llm(
            messages,
            temperature=DEFAULT_EXPAND_TEMPERATURE,
            max_tokens=DEFAULT_MAX_TOKENS,
        )
        return sanitize_ai_html(content[:MAX_CONTENT_LENGTH])

    async def create_branches(
        self,
        title: str,
        summary: str,
        branch_count: int = 2,
        branch_type: str = "choice",
        context: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Generate alternative story branches from a chapter."""
        system_prompt = (
            "You are a professional interactive fiction writer. "
            f"Create exactly {branch_count} alternative story branches "
            f"from the given chapter. Branch type: {branch_type}.\n\n"
            "For 'choice' type: each branch represents a different decision "
            "the protagonist could make.\n"
            "For 'condition' type: each branch represents a different "
            "circumstance that could unfold.\n\n"
            "Return a JSON array of branch objects, each with:\n"
            '- "title": string (branch chapter title)\n'
            '- "summary": string (2-3 sentence plot summary for this branch)\n'
            '- "branch_label": string (short label like "Fight" or "Flee")\n\n'
            "Return ONLY a JSON array, no other text."
        )

        user_prompt = f"Chapter: {title}\nSummary: {summary}"
        if context:
            user_prompt = f"Story context:\n{context}\n\n{user_prompt}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await self._call_llm(
            messages,
            temperature=DEFAULT_BRANCH_TEMPERATURE,
            max_tokens=DEFAULT_MAX_TOKENS,
        )
        branches = self._extract_json(response)

        if not isinstance(branches, list):
            raise ValueError("LLM did not return a JSON array")

        sanitized: List[Dict[str, str]] = []
        for i, b in enumerate(branches[:branch_count]):
            if not isinstance(b, dict):
                continue
            # Ensure values are strings, not nested structures
            title = b.get("title", f"Branch {i + 1}")
            if not isinstance(title, str):
                title = str(title)[:MAX_TITLE_LENGTH]
            else:
                title = title[:MAX_TITLE_LENGTH]
            summary = b.get("summary", "")
            if not isinstance(summary, str):
                summary = str(summary)[:MAX_SUMMARY_LENGTH]
            else:
                summary = summary[:MAX_SUMMARY_LENGTH]
            branch_label = b.get("branch_label", f"Path {i + 1}")
            if not isinstance(branch_label, str):
                branch_label = str(branch_label)[:MAX_BRANCH_LABEL_LENGTH]
            else:
                branch_label = branch_label[:MAX_BRANCH_LABEL_LENGTH]
            sanitized.append({
                "title": title,
                "summary": summary,
                "branch_label": branch_label,
            })
        return sanitized

    async def split_chapter_to_scenes(
        self,
        title: str,
        summary: str,
        content: Optional[str] = None,
        style_guide: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Split a chapter into 3-8 visual scenes for storyboard conversion."""
        system_prompt = (
            "You are a professional storyboard artist and visual storyteller. "
            "Split the given story chapter into 3-8 distinct visual scenes "
            "suitable for a storyboard.\n\n"
            "Each scene object must have:\n"
            '- "scene_number": int (sequential starting from 1)\n'
            '- "description": string (detailed visual description of the scene, '
            "what is happening, who is present, setting details)\n"
            '- "camera_notes": string (camera angle, shot type, mood, '
            "lighting suggestions)\n\n"
            "Return ONLY a JSON array, no other text."
        )

        user_prompt = f"Chapter title: {title}\nSummary: {summary}"
        if content:
            user_prompt += f"\n\nFull content:\n{content}"
        if style_guide:
            user_prompt += f"\n\nStyle guide:\n{style_guide}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await self._call_llm(
            messages,
            temperature=DEFAULT_OUTLINE_TEMPERATURE,
            max_tokens=DEFAULT_MAX_TOKENS,
        )
        scenes = self._extract_json(response)

        if not isinstance(scenes, list):
            raise ValueError("LLM did not return a JSON array")

        return [
            {
                "scene_number": s.get("scene_number", i + 1),
                "description": s.get("description", ""),
                "camera_notes": s.get("camera_notes", ""),
            }
            for i, s in enumerate(scenes[:8])
        ]
