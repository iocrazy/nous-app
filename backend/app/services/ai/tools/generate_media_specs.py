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
                        "description": "Optional provider override.",
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
                "Generate a short video. Saved to the user's Generations library; a "
                "reference is returned. Requires a source image URL to animate."
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
