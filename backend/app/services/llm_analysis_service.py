# backend/app/services/llm_analysis_service.py

"""
LLM analysis service.

Provides:
- Transcript summarization (summary, key points, topics)
- Visual analysis via multimodal LLM (planned)
"""

import json
from dataclasses import dataclass
from typing import List, Optional

from loguru import logger

from app.repositories.ai_repository import AIRepository
from app.services.ai_provider import AIProviderFactory


@dataclass
class SummaryResult:
    summary: str
    key_points: List[str]
    topics: List[str]


class LLMAnalysisService:
    """LLM-powered analysis for video transcripts and content."""

    def __init__(self, provider_key: str = "openai", provider_config: dict = None):
        """
        Args:
            provider_key: AI provider key (e.g. 'openai', 'deepseek').
            provider_config: Config dict for the provider.
        """
        self._provider_key = provider_key
        self._provider_config = provider_config or {}
        self._repo = AIRepository()

    async def generate_summary(
        self,
        transcript_text: str,
        video_info: dict = None,
        model: str = None,
    ) -> SummaryResult:
        """Generate a summary from a transcript.

        Args:
            transcript_text: Full transcript text.
            video_info: Optional dict with title, description, author, etc.
            model: Override the default model for this request.

        Returns:
            SummaryResult with summary, key_points, topics.
        """
        provider = AIProviderFactory.get_provider(
            self._provider_key, self._provider_config
        )

        context_parts = []
        if video_info:
            if video_info.get("title"):
                context_parts.append(f"Title: {video_info['title']}")
            if video_info.get("description"):
                context_parts.append(f"Description: {video_info['description']}")
            if video_info.get("author"):
                context_parts.append(f"Author: {video_info['author']}")

        context_str = "\n".join(context_parts) if context_parts else ""

        system_prompt = (
            "You are a helpful assistant that summarizes video transcripts. "
            "Return your response as valid JSON with exactly these keys:\n"
            '- "summary": A 2-3 sentence summary of the video content.\n'
            '- "key_points": A list of 3-5 key points as strings.\n'
            '- "topics": A list of 3-7 topic tags as strings.\n'
            "Only return the JSON object, nothing else."
        )

        user_message = "Summarize this video transcript.\n\n"
        if context_str:
            user_message += f"Video info:\n{context_str}\n\n"
        user_message += f"Transcript:\n{transcript_text[:8000]}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        logger.info(f"Generating summary with {self._provider_key}")
        response_text = await provider.chat(messages, model=model)

        return self._parse_summary_response(response_text)

    async def generate_summary_and_save(
        self,
        resource_id: str,
        transcript_text: str,
        video_info: dict = None,
        model: str = None,
    ) -> Optional[SummaryResult]:
        """Generate summary and persist to database.

        Args:
            resource_id: ID of the resource to associate the summary with.
            transcript_text: Full transcript text.
            video_info: Optional video metadata (title, description, author).
            model: LLM model name.
        """
        try:
            result = await self.generate_summary(transcript_text, video_info, model)

            await self._repo.save_summary(
                resource_id,
                {
                    "summary_type": "transcript",
                    "summary_text": result.summary,
                    "key_points": result.key_points,
                    "topics": result.topics,
                    "llm_model": model or self._provider_config.get("model", ""),
                    "llm_provider": self._provider_key,
                },
            )

            logger.info(f"Summary saved for resource {resource_id}")
            return result

        except Exception as e:
            logger.error(f"Summary generation failed for resource {resource_id}: {e}")
            raise

    def _parse_summary_response(self, text: str) -> SummaryResult:
        """Parse LLM JSON response into SummaryResult."""
        # Strip markdown code fences if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first and last lines (fences)
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)

        try:
            data = json.loads(cleaned)
            return SummaryResult(
                summary=data.get("summary", ""),
                key_points=data.get("key_points", []),
                topics=data.get("topics", []),
            )
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse LLM response as JSON, using raw text as summary"
            )
            return SummaryResult(
                summary=text.strip()[:500],
                key_points=[],
                topics=[],
            )
