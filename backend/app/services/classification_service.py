"""Content classification service using keyword matching and AI."""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from loguru import logger

from app.repositories.tags_repository import TagsRepository


@dataclass
class ClassificationResult:
    """Result of content classification."""

    primary_tag: str
    confidence: float
    secondary_tag: Optional[str] = None
    source: str = "auto"  # "auto" for keyword, "ai" for AI


# Keyword mapping for auto-classification
KEYWORD_MAPPING = {
    "Food": {
        "keywords_cn": [
            "美食",
            "做饭",
            "菜谱",
            "厨房",
            "吃",
            "烹饪",
            "料理",
            "食材",
            "炒菜",
            "烘焙",
        ],
        "keywords_en": ["food", "cook", "recipe", "kitchen", "eat", "dish", "meal"],
    },
    "Tutorial": {
        "keywords_cn": [
            "教程",
            "教学",
            "学习",
            "怎么",
            "如何",
            "教你",
            "学会",
            "技巧",
            "方法",
        ],
        "keywords_en": ["tutorial", "learn", "how to", "guide", "tips", "lesson"],
    },
    "Comedy": {
        "keywords_cn": ["搞笑", "笑死", "哈哈", "段子", "沙雕", "整蛊", "恶搞", "幽默"],
        "keywords_en": ["funny", "comedy", "lol", "joke", "humor", "laugh"],
    },
    "Dance": {
        "keywords_cn": ["舞蹈", "跳舞", "热舞", "编舞", "舞步", "街舞", "广场舞"],
        "keywords_en": ["dance", "dancing", "choreography", "dancer"],
    },
    "Music": {
        "keywords_cn": ["音乐", "唱歌", "翻唱", "原创", "歌曲", "演唱", "歌词"],
        "keywords_en": ["music", "sing", "cover", "song", "vocal", "melody"],
    },
    "Beauty": {
        "keywords_cn": ["美妆", "化妆", "护肤", "变美", "妆容", "口红", "眼影"],
        "keywords_en": ["makeup", "beauty", "skincare", "cosmetic"],
    },
    "Fashion": {
        "keywords_cn": ["穿搭", "时尚", "衣服", "搭配", "潮流", "服装", "ootd"],
        "keywords_en": ["fashion", "outfit", "style", "clothes", "wear"],
    },
    "Gaming": {
        "keywords_cn": ["游戏", "电竞", "主播", "直播", "玩家", "手游", "端游"],
        "keywords_en": ["game", "gaming", "esports", "player", "streamer"],
    },
    "Pets": {
        "keywords_cn": ["宠物", "猫", "狗", "萌宠", "猫咪", "狗狗", "铲屎官"],
        "keywords_en": ["pet", "cat", "dog", "cute", "puppy", "kitten"],
    },
    "Travel": {
        "keywords_cn": ["旅行", "旅游", "风景", "打卡", "景点", "出行", "游玩"],
        "keywords_en": ["travel", "trip", "scenery", "tour", "journey"],
    },
    "Tech": {
        "keywords_cn": ["科技", "数码", "测评", "开箱", "手机", "电脑", "评测"],
        "keywords_en": ["tech", "digital", "review", "unbox", "gadget"],
    },
    "Sports": {
        "keywords_cn": ["运动", "健身", "篮球", "足球", "跑步", "锻炼", "健康"],
        "keywords_en": ["sports", "fitness", "gym", "workout", "exercise"],
    },
    "Vlog": {
        "keywords_cn": ["vlog", "日常", "生活", "记录", "一天", "日记"],
        "keywords_en": ["vlog", "daily", "life", "routine", "day in"],
    },
}


class ClassificationService:
    """Service for auto-classifying video content."""

    @staticmethod
    def classify_by_keywords(
        title: str,
        description: Optional[str] = None,
        original_tags: Optional[List[str]] = None,
    ) -> ClassificationResult:
        """
        Classify content using keyword matching.
        Returns classification result with confidence score.
        """
        # Combine all text for matching
        text_parts = [title]
        if description:
            text_parts.append(description)
        if original_tags:
            text_parts.extend(original_tags)

        combined_text = " ".join(text_parts).lower()

        # Score each category
        scores: List[Tuple[str, int]] = []

        for category, keywords in KEYWORD_MAPPING.items():
            score = 0

            # Check Chinese keywords
            for kw in keywords["keywords_cn"]:
                if kw in combined_text:
                    score += 2  # Higher weight for Chinese (primary language)

            # Check English keywords
            for kw in keywords["keywords_en"]:
                if kw in combined_text:
                    score += 1

            if score > 0:
                scores.append((category, score))

        if not scores:
            return ClassificationResult(
                primary_tag="Other", confidence=0.3, source="auto"
            )

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)

        primary = scores[0]
        secondary = scores[1] if len(scores) > 1 else None

        # Calculate confidence based on score difference
        max_possible_score = 20  # Approximate max score
        confidence = min(primary[1] / max_possible_score, 0.95)

        # Boost confidence if significantly higher than second place
        if secondary and primary[1] > secondary[1] * 2:
            confidence = min(confidence + 0.1, 0.95)

        return ClassificationResult(
            primary_tag=primary[0],
            confidence=round(confidence, 2),
            secondary_tag=secondary[0] if secondary else None,
            source="auto",
        )

    @staticmethod
    async def auto_tag_video(
        video_id: int,
        title: str,
        description: Optional[str] = None,
        original_tags: Optional[List[str]] = None,
        min_confidence: float = 0.3,
    ) -> List[dict]:
        """
        Automatically tag a video based on its content.
        Returns list of tags added.
        """
        result = ClassificationService.classify_by_keywords(
            title=title, description=description, original_tags=original_tags
        )

        added_tags = []
        repo = TagsRepository()

        # Get the system tag
        primary_tag = await repo.get_tag_by_name(result.primary_tag)

        if primary_tag and result.confidence >= min_confidence:
            await repo.add_tag_to_video(
                video_id=video_id,
                tag_id=primary_tag["id"],
                confidence=result.confidence,
                source=result.source,
            )
            added_tags.append({"tag": primary_tag, "confidence": result.confidence})
            logger.info(
                f"Auto-tagged video {video_id} as '{result.primary_tag}' (confidence: {result.confidence})"
            )

        # Add secondary tag if confidence is reasonable
        if result.secondary_tag and result.confidence >= 0.5:
            secondary_tag = await repo.get_tag_by_name(result.secondary_tag)
            if secondary_tag:
                secondary_confidence = round(
                    result.confidence * 0.7, 2
                )  # Lower confidence for secondary
                await repo.add_tag_to_video(
                    video_id=video_id,
                    tag_id=secondary_tag["id"],
                    confidence=secondary_confidence,
                    source=result.source,
                )
                added_tags.append(
                    {"tag": secondary_tag, "confidence": secondary_confidence}
                )
                logger.info(
                    f"Auto-tagged video {video_id} with secondary tag '{result.secondary_tag}'"
                )

        return added_tags
