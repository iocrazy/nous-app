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


# Shared by ProposeEdit and ApplyEdit — they take the SAME target, and a
# model that learned one shape must not have to learn a second (A5).
_EDIT_PARAMS: dict[str, dict] = {
    "scene_id": _SCENE_ID,
    "element_ids": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "element_id values from ReadScene naming the passage you are "
            "rewriting. Ignored when the writer attached a selection to this "
            "turn — theirs wins."
        ),
    },
    "edits": {
        "type": "array",
        "description": (
            "The replacement text, bound to the element it replaces. One "
            "entry per element you are changing."
        ),
        "items": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["element_id", "text"],
        },
    },
    "rationale": {
        "type": "string",
        "description": "One sentence: why this change.",
    },
    "base_content_version": {
        "type": "integer",
        "description": (
            "The content_version ReadScene returned for this scene. This is "
            "the proof you are rewriting the text you actually read: if the "
            "writer changed those same elements meanwhile, the edit is "
            "refused instead of overwriting them."
        ),
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
                "element_ids you name, having checked that the proposal could "
                "be applied right now. Quote the content_version you got from "
                "ReadScene so a proposal the writer has already overtaken "
                "comes back flagged stale."
            ),
            "parameters": {
                "type": "object",
                "properties": dict(_EDIT_PARAMS),
                "required": ["scene_id", "element_ids", "edits"],
            },
        },
    }


def generate_shot_image_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "GenerateShotImage",
            "description": (
                "Dispatch AI image generation for one existing shot card, "
                "using its cinematography tags, description, and scene "
                "heading as the prompt. This call is ASYNCHRONOUS: it only "
                "confirms the generation was dispatched — the produced image "
                "lands on the shot some time after this call returns, not in "
                "its result. It costs real money per call, so do not call it "
                "again for the same shot_id just because you have not seen "
                "the result yet; wait and re-read the shot instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "shot_id": {
                        "type": "string",
                        "description": (
                            "Shot handle from ReadScene's shots list or "
                            "CreateShot's result. Never construct or guess one."
                        ),
                    },
                },
                "required": ["shot_id"],
            },
        },
    }


def apply_edit_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "ApplyEdit",
            "description": (
                "Write a revision into the script, replacing the text of the "
                "elements you name. Read the scene first and pass the "
                "content_version it returned as base_content_version — it is "
                "REQUIRED. If the writer changed those same elements while "
                "you were working, nothing is written and you get their "
                "current text back to rebase on; if they changed something "
                "else in the scene, your edit is applied on top of their "
                "work. Element type is preserved — you can rewrite a line of "
                "dialogue, not turn it into an action line."
            ),
            "parameters": {
                "type": "object",
                "properties": dict(_EDIT_PARAMS),
                "required": [
                    "scene_id",
                    "element_ids",
                    "edits",
                    "base_content_version",
                ],
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
    "ApplyEdit": apply_edit_spec,
    "GenerateShotImage": generate_shot_image_spec,
}


def screenwriting_tool_specs(
    write_level: str, *, media_image_allowed: bool = False
) -> list[dict]:
    """Specs to advertise to an agent granted ``write_level`` (+ optionally
    the ``media.image`` capability, for ``GenerateShotImage``).

    A UX filter ONLY — don't dangle a tool the agent cannot use in front of
    the model (same reasoning as the media-tool registration block in
    ai_library_chat_service.py). Advertising is never the enforcement: a
    stale cached system prompt or an outright hallucinated tool name reaches
    the dispatcher regardless of what this returned.

    What blocks those depends on where the turn runs (A4 review, Critical 1):
    on a runner carrying ``HighRiskCapabilityGateHook`` the gate re-checks
    the grant per call; on a runner built WITHOUT hooks — eight services do
    this, and they compose through the same ``PromptComposer`` that calls
    this function — there is no gate to re-check anything, so
    ``AgentRunner._dispatch_screenwriting`` refuses the call outright. Either
    way the answer is a refusal, never an ungated execution; do not read the
    filter below as the thing keeping an ungranted agent out.

    ``GenerateShotImage`` (A6) is graded on a DIFFERENT axis than the other
    five tools — ``TOOL_REQUIREMENTS["GenerateShotImage"].write_level`` is
    ``None`` (it costs money, it does not write script content), so the
    ordinal ``write_level`` filter below would advertise it unconditionally
    at every level including "none". ``media_image_allowed`` is therefore a
    SEPARATE, explicit gate the caller must compute (capability grant AND
    kill switch — mirrors the media-tool registration block in
    ai_library_chat_service.py) and pass in; a tool requiring ``media`` is
    only ever included when that flag is true, regardless of write_level.
    """
    from app.services.ai.permissions.high_risk_caps import HighRiskCaps
    from app.services.infra.hooks.high_risk_capability_gate import TOOL_REQUIREMENTS

    caps = HighRiskCaps(write_level=write_level)
    out: list[dict] = []
    for name, build in SCREENWRITING_TOOL_SPECS.items():
        requirement = TOOL_REQUIREMENTS.get(name)
        if requirement is not None and requirement.media is not None:
            if media_image_allowed:
                out.append(build())
            continue
        needed = requirement.write_level if requirement else None
        if needed is None or caps.meets_write_level(needed):
            out.append(build())
    return out


__all__ = [
    "SCREENWRITING_TOOL_SPECS",
    "screenwriting_tool_specs",
]
