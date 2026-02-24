# app/services/review_service.py

"""
Review system business logic.

Handles comment creation with annotations, status updates, and thread management.
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.repositories.review_repository import ReviewRepository


class ReviewService:
    """Business logic for the review system."""

    def __init__(self):
        self.repo = ReviewRepository()

    # ─── Comments ───────────────────────────────────────

    async def create_comment(
        self,
        resource_id: str,
        author_id: str,
        content: str,
        version_id: Optional[str] = None,
        timecode: Optional[float] = None,
        frame_number: Optional[int] = None,
        parent_id: Optional[str] = None,
        annotations: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Create a comment with optional annotations."""
        comment_data = {
            "resource_id": resource_id,
            "author_id": author_id,
            "content": content,
        }
        if version_id:
            comment_data["version_id"] = version_id
        if timecode is not None:
            comment_data["timecode"] = timecode
        if frame_number is not None:
            comment_data["frame_number"] = frame_number
        if parent_id:
            comment_data["parent_id"] = parent_id

        comment = await self.repo.create_comment(comment_data)
        logger.info(
            f"Created review comment {comment['id']} on resource {resource_id}"
        )

        # Create annotations if provided
        if annotations:
            annotation_records = [
                {
                    "comment_id": comment["id"],
                    "tool_type": a["tool_type"],
                    "data": a["data"],
                }
                for a in annotations
            ]
            created = await self.repo.create_annotations_batch(annotation_records)
            comment["annotations"] = created
        else:
            comment["annotations"] = []

        return comment

    async def get_comments(
        self,
        resource_id: str,
        version_id: Optional[str] = None,
        status: Optional[str] = None,
        include_replies: bool = True,
        include_annotations: bool = True,
    ) -> List[Dict[str, Any]]:
        """Get top-level comments with optional replies and annotations."""
        comments = await self.repo.get_comments_by_resource(
            resource_id, version_id, status
        )

        if include_replies or include_annotations:
            for comment in comments:
                if include_replies:
                    comment["replies"] = await self.repo.get_replies(comment["id"])
                    if include_annotations:
                        for reply in comment["replies"]:
                            reply["annotations"] = (
                                await self.repo.get_annotations_by_comment(reply["id"])
                            )
                if include_annotations:
                    comment["annotations"] = (
                        await self.repo.get_annotations_by_comment(comment["id"])
                    )

        return comments

    async def get_comment(
        self, comment_id: str, include_replies: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Get a single comment with replies."""
        comment = await self.repo.get_comment_by_id(comment_id)
        if not comment:
            return None

        comment["annotations"] = await self.repo.get_annotations_by_comment(
            comment_id
        )
        if include_replies:
            comment["replies"] = await self.repo.get_replies(comment_id)
            for reply in comment["replies"]:
                reply["annotations"] = await self.repo.get_annotations_by_comment(
                    reply["id"]
                )

        return comment

    async def update_comment(
        self, comment_id: str, user_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a comment (only by author)."""
        comment = await self.repo.get_comment_by_id(comment_id)
        if not comment:
            raise ValueError("Comment not found")
        if comment["author_id"] != user_id:
            raise PermissionError("Only the author can edit this comment")

        allowed = {"content", "status"}
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return comment

        return await self.repo.update_comment(comment_id, filtered)

    async def resolve_comment(
        self, comment_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Mark a comment as resolved (any authenticated user can resolve)."""
        comment = await self.repo.get_comment_by_id(comment_id)
        if not comment:
            raise ValueError("Comment not found")
        return await self.repo.update_comment(comment_id, {"status": "resolved"})

    async def reopen_comment(
        self, comment_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Reopen a resolved comment."""
        comment = await self.repo.get_comment_by_id(comment_id)
        if not comment:
            raise ValueError("Comment not found")
        return await self.repo.update_comment(comment_id, {"status": "open"})

    async def delete_comment(self, comment_id: str, user_id: str) -> bool:
        """Delete a comment (only by author). Cascades to replies and annotations."""
        comment = await self.repo.get_comment_by_id(comment_id)
        if not comment:
            raise ValueError("Comment not found")
        if comment["author_id"] != user_id:
            raise PermissionError("Only the author can delete this comment")

        result = await self.repo.delete_comment(comment_id)
        logger.info(f"Deleted review comment {comment_id}")
        return result

    async def get_comment_count(
        self, resource_id: str, version_id: Optional[str] = None
    ) -> int:
        return await self.repo.get_comment_count(resource_id, version_id)

    # ─── Review Status ──────────────────────────────────

    async def set_review_status(
        self,
        resource_id: str,
        reviewer_id: str,
        status: str,
        version_id: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set or update the review status for a resource."""
        valid_statuses = {"pending", "approved", "needs_changes", "rejected"}
        if status not in valid_statuses:
            raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")

        data = {
            "resource_id": resource_id,
            "reviewer_id": reviewer_id,
            "status": status,
        }
        if version_id:
            data["version_id"] = version_id
        if comment:
            data["comment"] = comment

        result = await self.repo.upsert_review_status(data)
        logger.info(
            f"Review status for resource {resource_id}: {status} by {reviewer_id}"
        )
        return result

    async def get_review_statuses(
        self, resource_id: str, version_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return await self.repo.get_review_statuses(resource_id, version_id)
