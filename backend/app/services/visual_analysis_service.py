"""Visual analysis service using OpenAI GPT-4o."""

import base64
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger
from openai import AsyncOpenAI


@dataclass
class VisualAnalysisResult:
    """Result of visual analysis."""

    category: str
    visual_description: str
    detected_objects: List[str] = field(default_factory=list)
    detected_scenes: List[str] = field(default_factory=list)
    detected_people: List[Dict[str, str]] = field(default_factory=list)
    detected_text: str = ""
    mood: str = ""
    cost: float = 0.0


# Prompts for different analysis levels
L1_PROMPT = """Analyze this video cover image and provide:

1. Main content category (choose ONE): Food, Tutorial, Comedy, Dance, Music, Beauty,
   Fashion, Gaming, Pets, Travel, Tech, Sports, Vlog, Other
2. Brief visual description (1-2 sentences)
3. Key objects visible (list up to 5)
4. Scene type (indoor/outdoor, specific location if identifiable)
5. People description if any (gender, clothing, action)
6. Any visible text (OCR)
7. Overall mood/style

Respond in JSON format:
{
  "category": "",
  "visual_description": "",
  "detected_objects": [],
  "detected_scenes": [],
  "detected_people": [{"gender": "", "clothing": "", "action": ""}],
  "detected_text": "",
  "mood": ""
}"""

L2_PROMPT = """Analyze these video keyframes (cover + 3 frames at 25%, 50%, 75% of video).

Provide a comprehensive analysis:
1. Main content category
2. Detailed visual description covering all frames
3. All objects visible across frames
4. Scene transitions or changes
5. People and their actions throughout
6. Any text visible (subtitles, captions, on-screen text)
7. Overall narrative/content summary

Respond in JSON format:
{
  "category": "",
  "visual_description": "",
  "detected_objects": [],
  "detected_scenes": [],
  "detected_people": [{"gender": "", "clothing": "", "action": ""}],
  "detected_text": "",
  "mood": "",
  "content_summary": ""
}"""


class VisualAnalysisService:
    """Service for analyzing video content using AI."""

    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, visual analysis will be disabled")
            self.client = None
        else:
            self.client = AsyncOpenAI(api_key=api_key)

        self.model = os.getenv("OPENAI_MODEL", "gpt-4o")

    async def _encode_image_from_url(self, url: str) -> Optional[str]:
        """Download and encode image to base64."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=30.0)
                if response.status_code == 200:
                    return base64.b64encode(response.content).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to download image from {url}: {e}")
        return None

    async def _encode_image_from_file(self, file_path: str) -> Optional[str]:
        """Read and encode local image to base64."""
        try:
            import aiofiles

            async with aiofiles.open(file_path, "rb") as f:
                data = await f.read()
            return base64.b64encode(data).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to read image from {file_path}: {e}")
        return None

    async def analyze_l1(self, cover_url: str) -> Optional[VisualAnalysisResult]:
        """
        L1 Analysis: Cover image only.
        Cost: ~$0.001 per image
        """
        if not self.client:
            logger.warning("OpenAI client not initialized, skipping L1 analysis")
            return None

        image_data = await self._encode_image_from_url(cover_url)
        if not image_data:
            logger.error(f"Failed to encode cover image: {cover_url}")
            return None

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": L1_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_data}",
                                    "detail": "low",  # Low detail for cost efficiency
                                },
                            },
                        ],
                    }
                ],
                max_tokens=500,
                response_format={"type": "json_object"},
            )

            # Parse response
            content = response.choices[0].message.content
            data = json.loads(content)

            # Calculate cost (approximate)
            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens
            cost = (
                input_tokens * 0.0025 + output_tokens * 0.01
            ) / 1000  # GPT-4o pricing

            return VisualAnalysisResult(
                category=data.get("category", "Other"),
                visual_description=data.get("visual_description", ""),
                detected_objects=data.get("detected_objects", []),
                detected_scenes=data.get("detected_scenes", []),
                detected_people=data.get("detected_people", []),
                detected_text=data.get("detected_text", ""),
                mood=data.get("mood", ""),
                cost=cost,
            )

        except Exception as e:
            logger.error(f"L1 analysis failed: {e}")
            return None

    async def analyze_l2(
        self, cover_url: str, keyframe_paths: List[str]
    ) -> Optional[VisualAnalysisResult]:
        """
        L2 Analysis: Cover + keyframes.
        Cost: ~$0.005 per video
        """
        if not self.client:
            logger.warning("OpenAI client not initialized, skipping L2 analysis")
            return None

        # Encode all images
        images = []

        cover_data = await self._encode_image_from_url(cover_url)
        if cover_data:
            images.append(f"data:image/jpeg;base64,{cover_data}")

        for path in keyframe_paths:
            frame_data = await self._encode_image_from_file(path)
            if frame_data:
                images.append(f"data:image/jpeg;base64,{frame_data}")

        if not images:
            logger.error("No images available for L2 analysis")
            return None

        try:
            content: List[Dict[str, Any]] = [{"type": "text", "text": L2_PROMPT}]
            for img in images:
                content.append(
                    {"type": "image_url", "image_url": {"url": img, "detail": "low"}}
                )

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                max_tokens=800,
                response_format={"type": "json_object"},
            )

            data = json.loads(response.choices[0].message.content)

            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens
            cost = (input_tokens * 0.0025 + output_tokens * 0.01) / 1000

            return VisualAnalysisResult(
                category=data.get("category", "Other"),
                visual_description=data.get("visual_description", ""),
                detected_objects=data.get("detected_objects", []),
                detected_scenes=data.get("detected_scenes", []),
                detected_people=data.get("detected_people", []),
                detected_text=data.get("detected_text", ""),
                mood=data.get("mood", ""),
                cost=cost,
            )

        except Exception as e:
            logger.error(f"L2 analysis failed: {e}")
            return None
