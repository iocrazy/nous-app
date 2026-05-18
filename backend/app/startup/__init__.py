"""Startup orchestration helpers for `main.lifespan`.

Each module owns one startup concern (config load, background tasks, DBOS
boot, SSRF proxy, agent framework primitives, lifecycle bus, etc). The
top-level `lifespan()` in `app.main` calls these in order. State that
needs to survive into shutdown is attached to `app.state`.

Pure refactor of the old monolithic `lifespan()` — behaviour unchanged,
sections moved to focused modules.
"""
