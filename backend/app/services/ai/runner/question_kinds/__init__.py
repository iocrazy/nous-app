"""Question kinds with server-side answer handlers (phase 2a §3).

Each module registers itself on import via ``question.register_kind``;
``question.py`` imports them at its bottom so the registry is complete as
soon as ``question`` is. ``user`` (plain AskUser) is registered inline there.
"""
