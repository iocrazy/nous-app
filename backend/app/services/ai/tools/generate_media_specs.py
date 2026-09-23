"""OpenAI function specs for the agent media-generation tools (sub-plan 5, Plan 2)."""

from __future__ import annotations


def generate_image_tool_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "GenerateImage",
            "description": (
                "Generate an image from a text prompt. The image is saved to the "
                "user's Generations library and a reference is returned. Use when "
                "the user asks you to create / draw / render an image."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "What to depict."},
                    "aspect_ratio": {
                        "type": "string",
                        "description": "Optional, e.g. '16:9', '1:1'. Default 16:9.",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model override.",
                    },
                    "provider": {
                        "type": "string",
                        "description": (
                            "Which image provider to use: a catalog model "
                            "name, or a model id / provider key the user "
                            "enabled under Settings → AI (e.g. 'doubao'). "
                            "Without it, resolution falls back to the "
                            "platform catalog, which may have no image "
                            "model enabled."
                        ),
                    },
                },
                "required": ["prompt"],
            },
        },
    }


def generate_video_tool_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "GenerateVideo",
            "description": (
                "Generate a short video from a source image. Asynchronous: this "
                "call only submits the job and returns a task_id at once. "
                "Rendering can take up to ~27 minutes; the finished video (its "
                "generated_media_id and url) or the failure reason arrives later "
                "as an inbox message. Do not poll, and do not re-submit the same "
                "request while it is rendering. The video is saved to the user's "
                "Generations library."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Motion / scene description.",
                    },
                    "source_image_url": {
                        "type": "string",
                        "description": "URL of the image to animate.",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model override.",
                    },
                    "provider": {
                        "type": "string",
                        "description": "Optional provider override.",
                    },
                },
                "required": ["prompt", "source_image_url"],
            },
        },
    }


__all__ = ["generate_image_tool_spec", "generate_video_tool_spec"]
