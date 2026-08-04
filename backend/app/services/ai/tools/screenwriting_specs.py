"""OpenAI function specs for the A4 screenwriting tools (agent-layer spec
§5.1).

Descriptions here are the model's ONLY documentation of these tools, so they
carry the two contracts the model must understand to use them well:

- ids are opaque handles obtained from ListScenes/ReadScene, never invented
  (a fabricated id is denied by the resolver and wastes a turn);
- the human-facing number is ``scene_no_in_episode`` — episode-unique and
  stable across inserts (spec §4.3's honest field name; never the ambiguous
  ``current_scene`` the design memo calls out as a bug source).

None of this is enforcement. Enforcement is the A1 capability gate at the
executor plus the A2 resolver inside each handler; a model that ignores every
word below still cannot reach a scene outside its run's scope.
"""

from __future__ import annotations

_SCENE_ID = {
    "type": "string",
    "description": (
        "Opaque scene handle from ListScenes/ReadScene. Never construct or "
        "guess one."
    ),
}

_SHOT_FIELDS: dict[str, dict] = {
    "shot_type": {
        "type": "string",
        "description": "Shot size, e.g. 'WIDE', 'MEDIUM', 'CLOSE UP', 'OTS'.",
    },
    "camera_angle": {
        "type": "string",
        "description": "e.g. 'EYE LEVEL', 'LOW ANGLE', 'HIGH ANGLE'.",
    },
    "camera_movement": {
        "type": "string",
        "description": "e.g. 'STATIC', 'PAN LEFT', 'DOLLY IN', 'HANDHELD'.",
    },
    "focal_length": {
        "type": "string",
        "description": "Lens, e.g. '24mm', '50mm', '85mm'.",
    },
    "lighting": {"type": "string", "description": "Lighting note for this shot."},
    "description": {
        "type": "string",
        "description": "One sentence describing what the shot shows.",
    },
}


def list_scenes_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "ListScenes",
            "description": (
                "List the scenes you can work on, in script order. Returns "
                "for each: scene_id (the handle to pass to other tools), "
                "scene_no_in_episode (the human-facing scene number, unique "
                "within its episode), INT/EXT, location, time of day, and "
                "whether the scene has any written content yet. Start here — "
                "you cannot address a scene without its scene_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "episode_id": {
                        "type": "string",
                        "description": (
                            "Optional: restrict to one episode. Omit to list "
                            "everything in reach."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Optional max rows (capped server-side).",
                    },
                },
                "required": [],
            },
        },
    }


def read_scene_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "ReadScene",
            "description": (
                "Read one scene's content. Returns its heading, its "
                "scene_no_in_episode, its existing shot cards, and its "
                "elements as an ordered list of {element_id, type, text}. "
                "element_id values are the anchors ProposeEdit addresses; "
                "content_version is the concurrency token to quote back when "
                "proposing an edit."
            ),
            "parameters": {
                "type": "object",
                "properties": {"scene_id": _SCENE_ID},
                "required": ["scene_id"],
            },
        },
    }


def create_shot_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "CreateShot",
            "description": (
                "Add one shot card to a scene's storyboard. The shot's number "
                "is assigned by the server (next in that scene) — do not pass "
                "one. Returns the created card including shot_label, the "
                "human-facing '<scene_no>-<shot_no>' reference."
            ),
            "parameters": {
                "type": "object",
                "properties": {"scene_id": _SCENE_ID, **_SHOT_FIELDS},
                "required": ["scene_id"],
            },
        },
    }


def update_shot_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "UpdateShot",
            "description": (
                "Revise an existing shot card's parameters or description. "
                "Only the fields you pass change. Cannot move, renumber, "
                "delete, or mark a card as rendered."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "shot_id": {
                        "type": "string",
                        "description": "Shot handle from ReadScene's shots list.",
                    },
                    **_SHOT_FIELDS,
                },
                "required": ["shot_id"],
            },
        },
    }


def propose_edit_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "ProposeEdit",
            "description": (
                "Propose a revision to specific elements of a scene, for the "
                "writer to accept or reject. This does NOT change the script "
                "— it returns a reviewable proposal anchored to the "
                "element_ids you name. Quote the content_version you got from "
                "ReadScene so the proposal can be detected as stale if the "
                "writer edits the scene meanwhile."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_id": _SCENE_ID,
                    "element_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "element_id values from ReadScene that this "
                            "proposal replaces."
                        ),
                    },
                    "proposed_text": {
                        "type": "string",
                        "description": "The replacement text you are proposing.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "One sentence: why this change.",
                    },
                    "base_content_version": {
                        "type": "integer",
                        "description": (
                            "The content_version ReadScene returned for this " "scene."
                        ),
                    },
                },
                "required": ["scene_id", "element_ids", "proposed_text"],
            },
        },
    }


# Name -> spec builder, in the order they are advertised. Also the canonical
# list of "which tools are the screenwriting set" — agent_runner dispatch and
# the A1 gate's TOOL_REQUIREMENTS both key off these names.
SCREENWRITING_TOOL_SPECS = {
    "ListScenes": list_scenes_spec,
    "ReadScene": read_scene_spec,
    "CreateShot": create_shot_spec,
    "UpdateShot": update_shot_spec,
    "ProposeEdit": propose_edit_spec,
}


def screenwriting_tool_specs(write_level: str) -> list[dict]:
    """Specs to advertise to an agent granted ``write_level``.

    A UX filter ONLY — don't dangle a tool the agent cannot use in front of
    the model (same reasoning as the media-tool registration block in
    ai_library_chat_service.py). The A1 gate re-checks every call regardless
    of what was advertised, so a stale cached system prompt or a hallucinated
    call is still blocked at the executor.
    """
    from app.services.ai.permissions.high_risk_caps import HighRiskCaps
    from app.services.infra.hooks.high_risk_capability_gate import TOOL_REQUIREMENTS

    caps = HighRiskCaps(write_level=write_level)
    out: list[dict] = []
    for name, build in SCREENWRITING_TOOL_SPECS.items():
        requirement = TOOL_REQUIREMENTS.get(name)
        needed = requirement.write_level if requirement else None
        if needed is None or caps.meets_write_level(needed):
            out.append(build())
    return out


__all__ = [
    "SCREENWRITING_TOOL_SPECS",
    "screenwriting_tool_specs",
]
