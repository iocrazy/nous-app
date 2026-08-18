"""Every ``extract_audio`` task must record whether it will transcribe.

``extract_audio`` is created from four places with two opposite meanings:
the manual transcribe endpoints dispatch it with ``chain_transcription=
True`` (a transcript WILL follow), while the post-download auto-chain and
the "Extract Audio" button leave it at the default (a transcript follows
only if the resource carries intent tags — production: 14/14 historical
rows are of that kind).

``chain_transcription`` is a frozen DBOS workflow **input**: nothing can
read it back off a running task and nothing can flip it. So each creation
point mirrors its own intent into ``metadata.chain_transcription``, which
the dedup and the read path branch on
(``services/ai/resource_ai_status.task_chains_transcription``).

If a creation point ever records an intent that disagrees with what it
actually dispatches, the lie propagates straight into a user-visible
answer: an audio-only run recorded as chaining makes the transcribe
endpoint reply "Transcription already in progress" to a request that then
produces no transcription at all — the silent no-op this pairing exists
to prevent.

This test reads the four call sites out of the AST and asserts the two
halves agree. It is deliberately structural: the runtime behaviour of the
two ai_router points is covered in test_transcribe_auto_extract_chain.py,
but download.py's and media_fetch_router's points sit inside a workflow
and a media endpoint that no unit test drives, so a flipped literal there
would otherwise turn nothing red.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

APP = Path(__file__).resolve().parents[2] / "app"

# file → the intent every extract_audio creation in it must carry
EXPECTED_INTENT = {
    "api/ai_router.py": [True, True],  # both manual transcribe endpoints
    "workflows/download.py": [False],  # post-download auto-extract
    "api/media_fetch_router.py": [False],  # "Extract Audio" button
}


def _kwarg(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _const(node) -> object:
    return node.value if isinstance(node, ast.Constant) else None


def _dict_lookup(node, key: str):
    """Value for ``key`` in a dict literal, or None."""
    if not isinstance(node, ast.Dict):
        return None
    for k, v in zip(node.keys, node.values):
        if _const(k) == key:
            return v
    return None


def _extract_audio_sites(tree: ast.AST) -> tuple[list, list]:
    """(recorded intents, dispatched intents) in source order."""
    recorded: list = []
    dispatched: list = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # ``…create(task_type="extract_audio", …, metadata={...})``
        if _const(_kwarg(node, "task_type")) == "extract_audio":
            meta = _kwarg(node, "metadata")
            recorded.append(
                (node.lineno, _const(_dict_lookup(meta, "chain_transcription")))
            )

        # ``start_workflow_routed("extract_audio", dbos_workflow_kwargs={...})``
        if (
            getattr(node.func, "id", None) == "start_workflow_routed"
            or getattr(node.func, "attr", None) == "start_workflow_routed"
        ) and node.args:
            if _const(node.args[0]) == "extract_audio":
                wf_kwargs = _kwarg(node, "dbos_workflow_kwargs")
                chain = _const(_dict_lookup(wf_kwargs, "chain_transcription"))
                # Absent means the workflow's own default, which is False.
                dispatched.append((node.lineno, bool(chain)))

    recorded.sort()
    dispatched.sort()
    return recorded, dispatched


def _files_creating_extract_audio() -> set[str]:
    """Every app/ file that creates an extract_audio task_tracking row."""
    found = set()
    for path in APP.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - app/ must always parse
            continue
        recorded, _ = _extract_audio_sites(tree)
        if recorded:
            found.add(path.relative_to(APP).as_posix())
    return found


def test_no_fifth_creation_point_appeared_somewhere_else():
    """The whole invariant is per-creation-point, so a new one landing in a
    file this test does not know about would be invisible: it would record
    no intent, every reader would call it non-chaining, and a genuine
    chaining run would start telling users to retry for nothing.

    Scanning all of app/ rather than the fixed list is the difference
    between "the four we know about are consistent" and "there are only
    four". Adding a creation point is fine — add it to EXPECTED_INTENT with
    the intent it dispatches, and the pairing test covers it from then on.
    """
    assert _files_creating_extract_audio() == set(EXPECTED_INTENT)


@pytest.mark.parametrize("rel_path", sorted(EXPECTED_INTENT))
def test_every_extract_audio_creation_records_its_chain_intent(rel_path):
    tree = ast.parse((APP / rel_path).read_text())
    recorded, _ = _extract_audio_sites(tree)

    assert recorded, f"no extract_audio creation found in {rel_path}"
    for lineno, intent in recorded:
        assert intent is not None, (
            f"{rel_path}:{lineno} creates an extract_audio task without "
            "metadata['chain_transcription']. Readers cannot then tell a run "
            "that will transcribe from one that will not, and default to the "
            "safe answer — which for the transcribe endpoint means telling "
            "the user to retry a task that was in fact going to chain."
        )
    assert [intent for _, intent in recorded] == EXPECTED_INTENT[rel_path]


@pytest.mark.parametrize("rel_path", sorted(EXPECTED_INTENT))
def test_the_recorded_intent_matches_what_is_actually_dispatched(rel_path):
    """The pairing that matters: the metadata is only useful while it keeps
    agreeing with the ``chain_transcription`` argument next to it."""
    tree = ast.parse((APP / rel_path).read_text())
    recorded, dispatched = _extract_audio_sites(tree)

    assert len(recorded) == len(dispatched), (
        f"{rel_path}: {len(recorded)} extract_audio creations but "
        f"{len(dispatched)} dispatches — the pairing below cannot be trusted"
    )
    for (create_line, intent), (dispatch_line, chain) in zip(recorded, dispatched):
        assert intent == chain, (
            f"{rel_path}: the task created at line {create_line} records "
            f"chain_transcription={intent!r}, but the workflow dispatched at "
            f"line {dispatch_line} runs with chain_transcription={chain!r}. "
            "One of them is lying, and the dedup/read path believes the "
            "metadata."
        )
