"""SubAgent Task tool — synchronous spawn-and-return.

Phase 3b of issue #199. The main agent calls
``Skill(skill="task", subagent_type=..., prompt=...)`` and the call
blocks until the sub-agent has finished its turn, then returns a
compact envelope. Distinct from ``DelegateToolService``:

  - DelegateTool — workforce / inbox-based / target must be persistent
    / can run async-await across many turns
  - SubAgentTask — same-turn / same-process / ephemeral / target needs
    no persistent flag / returns one self-contained summary

Rationale: an agent that's mid-turn often needs to fan out a chunk of
self-contained work (research, summarization, code-search) without
flooding its own context. The pattern is well-known — Claude Code's
``Task`` tool, Deep Agents' ``sub_agent`` — and is the design Phase 1
+ Phase 2 of #199 prepared the ground for (compactor frees room,
summarizer can shrink the head, sub-agent isolates the tangent).

What this DOES:
  - Verify caller depth + cycle (reuses DelegateToolService limits +
    parent-run walk so two ways of spawning sub-agents can't bypass
    each other)
  - Build a fresh AgentRunner via ``build_agent_runner_stack`` —
    same wiring chat layer uses for top-level turns, just tagged with
    the parent run id and a ``+1`` depth
  - Compose the sub-agent's system prompt via PromptComposer
  - Run one ``run_turn`` to completion under a child RunRecorder
  - Wire ``agent_runs.parent_run_id`` so Runs tab can render a tree
  - Return a compact envelope (summary / status / sub_run_id / cost)

What this does NOT do (deferred to Phase 5+):
  - Stream sub-agent progress back to the parent agent (D5: v1 returns
    after completion, no mid-flight events)
  - Handle multi-turn sub-conversations — ``run_turn`` runs once

M2-b (Phase 4.5): parallel fan-out. ``Skill(skill="task", tasks=[...])``
spawns each entry concurrently, capped by the caller agent's
``capability_profile.max_parallel_delegates`` (default 3, list length
hard-capped at MAX_FANOUT). The single-task form is unchanged.

Phase 2b-2 §2 adds two more shapes to the same tool:

  - ``await=false`` — BACKGROUND. Instead of running the child here, write a
    queued ``task_tracking`` row and return immediately; the workforce tick
    picks it up within ~10s, runs the very same ``_spawn(await=True)``, and
    delivers the envelope to the parent's target as an
    ``agent_run_inbox`` row of kind ``subagent_result``. This call NEVER
    enqueues a DBOS workflow itself: the parent's turn already runs inside a
    DBOS step, and enqueueing from inside a step is the in-step dispatch the
    spec forbids.
  - ``child_run_id`` — CONTINUE. Rebuild an earlier child's messages from its
    transcript and run one more turn, recorded as a fork of that run so the
    Runs tree shows the rounds in order.

Both emit ``subagent_spawned`` / ``subagent_done`` on the PARENT run, so a
trajectory reads the same whether a child ran here or in the background. The
background ``subagent_done`` is written by the worker, long after the parent
run may have ended — events outlive runs, and the fold tolerates a ``done``
with no matching ``spawned``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional
from uuid import UUID

from loguru import logger

# Reuse DelegateToolService's depth + rate-limit + cycle helpers so
# the two spawning paths share the same safety net.
from app.services.workforce.delegate_tool import (
    MAX_DELEGATION_DEPTH,
    _check_rate_limit,
)

# Envelope keys returned to the parent agent. Pinned in tests so
# downstream consumers (parent agent prompt, frontend Runs UI) can
# rely on the shape.
ENVELOPE_KEYS = (
    "summary",
    "key_findings",
    "files_created",
    "tokens_used",
    "sub_run_id",
    "status",
)

# M2-b parallel fan-out limits. DEFAULT_MAX_PARALLEL applies when the
# caller agent has no capability_profile.max_parallel_delegates; MAX_FANOUT
# bounds the tasks list itself (concurrency is the semaphore's job, this
# is an abuse guard — the per-caller rate limit still applies per child).
DEFAULT_MAX_PARALLEL = 3
MAX_FANOUT = 10

# Parent-chain walk bound for ``child_run_id``. Same reasoning as
# ``delegate_tool._detect_cycle``: a data cycle (two rows pointing at each
# other) must not spin the walker forever, and no legitimate chain is deeper
# than the delegation depth cap anyway.
MAX_PARENT_HOPS = 10


def _tokens_of(recorder: Any) -> int:
    """Whole-sub-turn token total from the child's own counters — the same
    figure ``_build_envelope`` reports on the success path."""
    try:
        return int(getattr(recorder, "prompt_tokens", 0) or 0) + int(
            getattr(recorder, "completion_tokens", 0) or 0
        )
    except Exception:  # noqa: BLE001 — telemetry never fails a turn
        return 0


def _as_int(raw: Any) -> Optional[int]:
    """A Snowflake id as an int, or None when it is absent or unusable.

    Ids cross this module as ints (asyncpg BIGINT) and as strings (payloads,
    model arguments) interchangeably. None is the ONE answer for "no usable
    value": an ownership check that cannot read an id must refuse, and telling
    "absent" apart from "malformed" here would only give the caller a second
    way to say no."""
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _cost_cents_of(recorder: Any) -> float:
    """What the child cost, for the parent's ``cost.by_child`` breakdown.
    The recorder computes it from its own token counters; a stand-in that
    cannot is reported as 0.0 rather than crashing the emit."""
    try:
        compute = getattr(recorder, "compute_cost_cents", None)
        if callable(compute):
            return float(compute() or 0.0)
        return float(getattr(recorder, "cost_cents", 0.0) or 0.0)
    except Exception:  # noqa: BLE001 — telemetry never fails a turn
        return 0.0


class SubAgentTaskService:
    """Per-turn service: caller context baked in at construction.

    The parent agent's chat-wiring layer constructs one of these per
    turn (mirroring how DelegateToolService is wired) and hands it to
    the SkillToolService so the ``task`` built-in can dispatch.
    """

    def __init__(
        self,
        *,
        caller_agent_id: UUID,
        caller_user_id: UUID,
        # parent_run_id / session_id are BIGINT Snowflake ids (mig 231/232) → str.
        parent_run_id: Optional[str],
        agent_depth: int = 0,
        session_id: Optional[str] = None,
        parent_recorder: Optional[Any] = None,
        delegation_chain: tuple[str, ...] = (),
        max_parallel: int = DEFAULT_MAX_PARALLEL,
        issue_id: Optional[int] = None,
    ) -> None:
        self.caller_agent_id = caller_agent_id
        self.caller_user_id = caller_user_id
        self.parent_run_id = parent_run_id
        self.agent_depth = agent_depth
        self.session_id = session_id
        # M2: caller's slug chain (root → caller inclusive); spawned
        # children get this as their ancestor chain.
        self.delegation_chain = delegation_chain
        # M2-b: concurrency cap for the parallel ``tasks`` form. 0 means
        # the parallel form is disabled (CapabilityGate blocks the spawn
        # earlier; this is defense in depth).
        self.max_parallel = max_parallel
        # Phase 5 of #199: parent's RunRecorder so we can roll spawn
        # counts up to its metadata as note_subagent() calls. Optional
        # because not every caller hands us a recorder (CLI / batch
        # spawns may not have one); we only roll up when present.
        self.parent_recorder = parent_recorder
        # Phase 2b-2 §4.2: the issue this turn belongs to, inherited from the
        # parent run via the wiring. A sub-run of an issue run IS part of that
        # issue's tree; without this its agent_runs row has no issue link at
        # all (the post-turn backfill only ever sees root runs).
        self.issue_id = issue_id

    @property
    def active_parent_run_id(self) -> Optional[str]:
        """The run a child spawned NOW hangs off — resolved at spawn time.

        The constructor value answers a DIFFERENT question: which run this
        turn is itself a child of. ``build_agent_runner_stack`` passes ``None``
        there for a top-of-tree turn (its own docstring says so), so on every
        root issue / chat run the constructor value is empty — and taking the
        parent id from it sent the whole return chain out with ``null``: the
        child's ``agent_runs`` row never attached, ``subagent_done`` was never
        written back, ``view.children`` stayed at ``queued``, and
        ``_child_chain_ok`` refused the run's own child (2026-09-10 real-stack
        acceptance, defect 1).

        The run that is CURRENTLY executing is the recorder's row, so that is
        what a child hangs off — including a sub-agent spawning its own child,
        which must attach to ITSELF, not to the ancestor it inherited. The
        constructor value remains the fallback for the callers that have no
        recorder: the workforce worker rebuilds this service from a payload
        whose ``parent_run_id`` is already the right one, and a recorder whose
        insert failed carries no ``run_id`` to use.
        """
        rid = getattr(self.parent_recorder, "run_id", None)
        return str(rid) if rid else self.parent_run_id

    @property
    def active_issue_id(self) -> Optional[int]:
        """The issue this turn belongs to, resolved the same way
        ``active_parent_run_id`` is: the running recorder first, the
        constructor value as the fallback for callers that have none (the
        workforce worker rebuilds this service from a payload that already
        carries it). ``None`` on a conversation-scoped or probe run.

        Same source as ``_reply_target`` uses, deliberately: "which issue is
        this" must not have two answers within one service."""
        rec_issue = getattr(self.parent_recorder, "issue_id", None)
        return _as_int(rec_issue if rec_issue is not None else self.issue_id)

    async def spawn(self, args: dict[str, Any]) -> dict[str, Any]:
        """Public entry: dispatch + roll observability up to the
        parent recorder. Wrapping ``_spawn`` keeps the metadata
        side-effect on a single return path so any future early-exit
        added to ``_spawn`` automatically gets counted."""
        if args.get("tasks") is not None:
            # ``tasks`` starts N fresh children; ``child_run_id`` names exactly
            # one existing child. Together they have no meaning — refuse rather
            # than silently continue the same run N times.
            if args.get("child_run_id"):
                return self._failed("continue_not_allowed_in_fanout")
            return await self._spawn_parallel(args)
        envelope = await self._spawn(args)
        if self.parent_recorder is not None and hasattr(
            self.parent_recorder, "note_subagent"
        ):
            try:
                self.parent_recorder.note_subagent(envelope)
            except Exception:
                # Telemetry is best-effort — never break the parent's
                # tool dispatch loop over a metadata write.
                logger.exception(
                    "[subagent_task] note_subagent failed envelope_keys={}",
                    list(envelope.keys()),
                )
        # Structured log for observability dashboards (Phase 5 of #199).
        # loguru ``{}`` placeholder — issue #194 Bug D applies here too.
        logger.info(
            "[subagent.spawn] caller_agent_id={} caller_depth={} slug={} "
            "status={} sub_run_id={} tokens={}",
            self.caller_agent_id,
            self.agent_depth,
            args.get("subagent_type") or args.get("agent_slug"),
            envelope.get("status"),
            envelope.get("sub_run_id"),
            envelope.get("tokens_used"),
        )
        return envelope

    async def _spawn_parallel(self, args: dict[str, Any]) -> dict[str, Any]:
        """M2-b: fan out ``args["tasks"]`` concurrently.

        Each entry is the same shape as a single spawn's args
        (``subagent_type`` + ``prompt`` [+ ``description``]). Children
        run under a semaphore sized by ``max_parallel``; each goes
        through ``spawn`` so per-child telemetry (note_subagent + the
        structured log line) is identical to the single form. A crashing
        child becomes a failed envelope — it never sinks its siblings.

        ``await`` composes with the fan-out (spec §2.1): the top-level value
        is pushed into every entry, so ``tasks=[…], await=false`` enqueues
        each entry and returns at once. Ignoring the flag here would run the
        children synchronously while telling the model they were queued —
        a silently-dropped parameter, the worst of the three options. An
        entry may not override it, and may not carry ``child_run_id``: one
        continue names exactly one run, so a fan-out cannot express it.
        """
        tasks = args.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            return self._failed("tasks must be a non-empty list")
        if len(tasks) > MAX_FANOUT:
            return self._failed(
                f"too many tasks: {len(tasks)} exceeds the {MAX_FANOUT} fan-out cap"
            )
        if self.max_parallel <= 0:
            return self._failed(
                "parallel sub-agent spawning is disabled for this agent "
                "(max_parallel_delegates=0)"
            )

        semaphore = asyncio.Semaphore(self.max_parallel)

        want_await = args.get("await")

        async def run_one(task_args: Any) -> dict[str, Any]:
            if not isinstance(task_args, dict):
                return self._failed("each task must be an object")
            if task_args.get("child_run_id"):
                return self._failed("continue_not_allowed_in_fanout")
            entry = {**task_args}
            if want_await is not None:
                entry["await"] = want_await
            async with semaphore:
                try:
                    return await self.spawn(entry)
                except Exception as exc:  # noqa: BLE001 — sibling isolation
                    logger.exception(
                        "[subagent_task] parallel child crashed slug={}",
                        task_args.get("subagent_type"),
                    )
                    return self._failed(f"sub-agent crashed: {exc!s:.120}")

        results = list(await asyncio.gather(*(run_one(t) for t in tasks)))
        # ``queued`` counts as OK: the background form's success IS the queued
        # row. Reading it as a failure would report every background fan-out
        # as failed while every child is about to run.
        ok = sum(1 for r in results if r.get("status") in ("success", "queued"))
        if ok == len(results):
            status = "success"
        elif ok == 0:
            status = "failed"
        else:
            status = "partial"

        return {
            "status": status,
            "tasks_run": len(results),
            "results": results,
            "summary": f"{ok}/{len(results)} sub-agents dispatched",
        }

    async def _spawn(self, args: dict[str, Any]) -> dict[str, Any]:
        """Inner dispatch (the body of what was originally ``spawn``).

        ``args`` schema:
            subagent_type (str, required) — agent slug to spawn
            prompt (str, required)        — user-style instruction to
                                            the sub-agent
            description (str, optional)   — short label, currently
                                            surfaced in run metadata
                                            but not in the LLM call

        Returns an envelope dict with ENVELOPE_KEYS. Errors are
        returned as ``{"status": "failed", "error": ...}`` rather than
        raised so the parent agent's tool loop keeps moving — a
        crashing sub-agent must never crash its parent.
        """
        slug = (args.get("subagent_type") or args.get("agent_slug") or "").strip()
        prompt = (args.get("prompt") or "").strip()
        description = (args.get("description") or "").strip()

        if not slug:
            return self._failed("subagent_type required")
        if not prompt:
            return self._failed("prompt required")

        # Rate limit shares the DelegateTool sliding window per caller
        # — a runaway loop that spawns 30+ sub-agents in 60s gets cut
        # off the same way as runaway Delegate loops.
        rl_error = _check_rate_limit(self.caller_agent_id)
        if rl_error is not None:
            logger.warning(
                "[subagent_task] rate-limit hit caller={} slug={}",
                self.caller_agent_id,
                slug,
            )
            return self._failed(rl_error.get("error") or "rate limit")

        # Depth check before any DB roundtrip. agent_depth is the
        # CALLER's depth; the sub-agent will run at depth + 1, so we
        # reject when caller is already at the cap.
        if self.agent_depth >= MAX_DELEGATION_DEPTH:
            return self._failed(
                f"max sub-agent depth ({MAX_DELEGATION_DEPTH}) reached "
                f"at caller depth {self.agent_depth}"
            )

        # ``await`` defaults to True — the historical behaviour, and the one a
        # model that never heard of the flag keeps getting.
        want_await = args.get("await")
        want_await = True if want_await is None else bool(want_await)
        child_run_id = (str(args.get("child_run_id") or "")).strip() or None

        if not want_await:
            return await self._spawn_async(
                slug=slug,
                prompt=prompt,
                description=description,
                child_run_id=child_run_id,
            )

        # CONTINUE: the id came from the model, so ownership is verified
        # against the parent chain before a single transcript row is read.
        continue_from: Optional[tuple[list[dict[str, Any]], int]] = None
        if child_run_id is not None:
            if not await self._child_chain_ok(child_run_id):
                return self._failed("not_your_child")
            continue_from = await self._continue_messages(child_run_id, prompt)

        # Lazy import: pulls the full agent_runner stack which we don't
        # want at module-import time for any code path that doesn't
        # actually spawn sub-agents. Single try block + single
        # AgentRepository instance shared between the slug lookup and
        # the later compose step.
        try:
            from app.core.config import settings
            from app.repositories.agent_repository import get_agent_repository
            from app.repositories.skill_repository import get_skill_repository
            from app.services.ai.adapters.factory import provider_key_for_model
            from app.services.ai.chat.ai_library_chat_wiring import (
                build_agent_runner_stack,
            )
            from app.services.ai.prompts.prompt_composer import (
                ComposerInput,
                PromptComposer,
            )
            from app.services.ai.runner.run_recorder import RunRecorder
            from app.services.ai.scope.scope_binding import resolve_dispatch_scope

            # _attach_to_parent_run is private to agent_worker; keep an
            # eye on it during workforce refactors. The function writes
            # agent_runs.parent_run_id + root_run_id; if it ever moves
            # the import will fail loudly at the first task spawn.
            from app.services.workforce.agent_worker import _attach_to_parent_run
        except Exception as exc:
            logger.exception("[subagent_task] import wiring failed")
            return self._failed(f"import failed: {exc!s:.120}")

        agent_repo = get_agent_repository()

        # Reject self-spawn — same agent should branch via plan/loop,
        # not by recursing on itself. Delegate also rejects this; we
        # match for symmetry.
        try:
            target = await agent_repo.get_by_slug(slug)
        except Exception as exc:
            logger.exception("[subagent_task] agent lookup failed slug={}", slug)
            return self._failed(f"agent lookup failed: {exc!s:.120}")

        if not target:
            return self._failed(f"unknown agent slug: {slug!r}")

        target_agent_id = UUID(target["id"])
        if target_agent_id == self.caller_agent_id:
            return self._failed(
                "cannot spawn self as sub-agent; refactor as a plan step"
            )

        skill_repo = get_skill_repository()

        # Resolved ONCE per spawn and used for every consumer below (the
        # child's stack, its scope, its recorder metadata, the attach): the
        # four must never disagree about who the parent is.
        parent_run_id = self.active_parent_run_id

        try:
            stack = await build_agent_runner_stack(
                agent=target,
                skill_repo=skill_repo,
                user_id=self.caller_user_id,
                session_id=self.session_id,
                user_query=prompt,
                settings=settings,
                parent_run_id=parent_run_id,
                agent_depth=self.agent_depth + 1,
                delegation_chain=self.delegation_chain,
                issue_id=self.issue_id,
            )
        except Exception as exc:
            logger.exception("[subagent_task] stack build failed slug={}", slug)
            return self._failed(f"stack build failed: {exc!s:.120}")

        try:
            composer = PromptComposer(agent_repo, skill_repo)
            composed = await composer.compose(
                ComposerInput(
                    agent_slug=slug,
                    request_instructions=(
                        "You are running as a sub-agent spawned by a parent "
                        "agent that needs a self-contained answer to the "
                        "task below. Produce a complete response — the "
                        "parent only sees your final output, not your "
                        "intermediate steps."
                    ),
                )
            )
        except Exception as exc:
            logger.exception("[subagent_task] prompt compose failed slug={}", slug)
            return self._failed(f"prompt compose failed: {exc!s:.120}")

        model = composed.model or ""
        try:
            provider = provider_key_for_model(model) if model else None
        except ValueError:
            provider = None

        # A4: same inheritance rule as agent_worker — a spawned sub-agent gets
        # its parent's project AND episode, never a wider scope than the
        # parent it was spawned from. ``parent_run_id`` is the dispatcher's
        # own run id (server-side), never anything the model supplied in the
        # Task/Delegate arguments.
        dispatch_scope = await resolve_dispatch_scope(parent_run_id=parent_run_id)

        started = time.monotonic()
        # Set once ``subagent_spawned`` has gone out. The crash path below
        # reads it to decide whether it OWES a matching ``subagent_done``:
        # the fold only drains ``children.running`` on a done, so a child
        # announced-then-crashed would sit at "1 running" for the rest of the
        # parent's run — and a provider error is the most common way a child
        # ends. A failure BEFORE the announcement owes nothing; emitting a
        # done there would invent a child that never existed.
        announced_child_id: Optional[str] = None
        # Held alongside the id so the crash path can report what the child
        # actually burned. A run that died after ten tool calls still cost
        # money; reporting 0.0 understates exactly the runs worth noticing.
        announced_recorder: Optional[Any] = None
        try:
            async with RunRecorder(
                agent_id=composed.agent_id,
                user_id=self.caller_user_id,
                trigger="subagent_task",
                session_id=self.session_id,
                team_id=None,
                issue_id=self.issue_id,
                **dispatch_scope.as_recorder_kwargs(),
                model=model or None,
                provider=provider,
                input_summary=prompt[:240],
                # A continued round is recorded as a FORK of the run it
                # continues (mig 453 columns), so the Runs tree shows round 2
                # hanging off round 1 rather than as an unrelated sibling.
                fork_of_run_id=int(child_run_id) if child_run_id else None,
                fork_at_seq=continue_from[1] if continue_from else None,
                metadata={
                    "subagent_type": slug,
                    "description": description or None,
                    "parent_run_id": parent_run_id,
                    "agent_depth": self.agent_depth + 1,
                    "continued_from": child_run_id,
                    "round": (
                        (await self._round_of(child_run_id)) if child_run_id else 1
                    ),
                },
            ) as recorder:
                if parent_run_id is not None:
                    try:
                        await _attach_to_parent_run(
                            run_id=recorder.run_id,
                            parent_run_id=parent_run_id,
                            agent_depth=self.agent_depth + 1,
                        )
                    except Exception:
                        # Non-fatal: parent_run_id is for the Runs tab
                        # tree; losing it doesn't break the sub-run
                        # itself. Log full stack so post-mortem can see
                        # which write failed.
                        logger.exception(
                            "[subagent_task] _attach_to_parent_run failed "
                            "run_id={} parent_run_id={}",
                            getattr(recorder, "run_id", "?"),
                            parent_run_id,
                        )

                await self._emit_parent(
                    "subagent_spawned",
                    {
                        "child_run_id": str(recorder.run_id),
                        "task_id": None,
                        "mode": "sync",
                        "subagent_type": slug,
                        "description": description or "",
                        "continued_from": child_run_id,
                    },
                )
                announced_child_id = str(recorder.run_id)
                announced_recorder = recorder

                result = await stack.runner.run_turn(
                    composed,
                    user_messages=(
                        continue_from[0]
                        if continue_from
                        else [{"role": "user", "content": prompt}]
                    ),
                    recorder=recorder,
                )

                envelope = self._build_envelope(
                    result=result,
                    sub_run_id=recorder.run_id,
                    recorder=recorder,
                )
                await self._emit_parent(
                    "subagent_done",
                    {
                        "child_run_id": str(recorder.run_id),
                        "task_id": None,
                        "mode": "sync",
                        "subagent_type": slug,
                        "status": envelope["status"],
                        "cost_cents": _cost_cents_of(recorder),
                        "tokens_used": envelope["tokens_used"],
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                )
                return envelope
        except Exception as exc:
            logger.exception("[subagent_task] run_turn failed slug={}", slug)
            if announced_child_id is not None:
                await self._emit_parent(
                    "subagent_done",
                    {
                        "child_run_id": announced_child_id,
                        "task_id": None,
                        "mode": "sync",
                        "subagent_type": slug,
                        "status": "failed",
                        "cost_cents": _cost_cents_of(announced_recorder),
                        "tokens_used": _tokens_of(announced_recorder),
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                )
            return self._failed(f"sub-agent crashed: {exc!s:.120}")

    # ── background (await=false) ──────────────────────────────────────

    async def run_background_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The worker's entry point. Deliberately the SAME ``_spawn`` the
        synchronous form runs — a background child that behaved differently
        from a foreground one would be a second implementation to keep in
        step, and the difference the caller cares about (where the result
        goes) is the worker's job, not this one's."""
        return await self._spawn(
            {
                "subagent_type": payload.get("subagent_type"),
                "prompt": payload.get("prompt"),
                "description": payload.get("description") or "",
                "child_run_id": payload.get("child_run_id"),
                "await": True,
            }
        )

    def _reply_target(self) -> Optional[tuple[str, int]]:
        """Where a background child's result goes back to. The parent run's
        issue first, then its conversation; neither (a sub-agent's own
        sub-agent, a probe run) → None, and the caller refuses ``await=false``
        rather than running a billed turn nobody will ever read."""
        rec = self.parent_recorder
        issue_id = getattr(rec, "issue_id", None) or self.issue_id
        conv = getattr(rec, "conversation_id", None) or self.session_id
        for kind, raw in (("issue", issue_id), ("conversation", conv)):
            if not raw:
                continue
            try:
                return (kind, int(raw))
            except (TypeError, ValueError):
                # A target we cannot address is the same as no target. Every
                # other refusal on this path returns a typed envelope; letting
                # a ValueError escape into the tool dispatch loop would make
                # this the one that does not.
                logger.warning(
                    "[subagent_task] unusable {} reply target {!r}", kind, raw
                )
        return None

    async def _spawn_async(
        self,
        *,
        slug: str,
        prompt: str,
        description: str,
        child_run_id: Optional[str],
    ) -> dict[str, Any]:
        """Queue the child as a workforce task and return at once.

        No DBOS enqueue here: the parent's turn runs inside a DBOS step, and
        starting a workflow from inside one is the in-step dispatch spec §2.2
        forbids. Task 3's ``inbox_dispatch`` tick lists every queued,
        undispatched ``agent_task`` row and picks this one up within ~10s.
        """
        if self.agent_depth >= 1:
            # One level only. A background child cannot deliver a result to a
            # parent that has itself already finished, and depth-2 fan-out in
            # the background is how a runaway tree stops being observable.
            return self._failed("async_not_allowed_for_subagent")
        target = self._reply_target()
        if target is None:
            return self._failed("no_reply_target")

        agent_id = await self._resolve_agent_id(slug)
        if agent_id is None:
            return self._failed(f"unknown agent slug: {slug!r}")

        payload = {
            "kind": "subagent",
            "parent_run_id": self.active_parent_run_id,
            # The worker rebuilds this service from the payload; without the
            # caller's agent id it could not resolve depth or scope.
            "caller_agent_id": str(self.caller_agent_id),
            "subagent_type": slug,
            "prompt": prompt,
            "description": description or None,
            "child_run_id": child_run_id,
            "reply_to": {"target_kind": target[0], "target_id": target[1]},
            "user_id": str(self.caller_user_id),
            "agent_depth": self.agent_depth,
            # spec §2.5: a sub-run of an issue run IS part of that issue's
            # tree. The worker feeds this back into the child's recorder;
            # without it the child's agent_runs row has no issue link at all
            # (the post-turn backfill only ever sees root runs) and its spend
            # is invisible to the issue rollup.
            "issue_id": int(self.issue_id) if self.issue_id else None,
        }

        from app.repositories.agent_workforce_repository import (
            get_agent_workforce_repository,
        )

        try:
            row = await get_agent_workforce_repository().create_task(
                agent_id=agent_id,
                user_id=self.caller_user_id,
                payload=payload,
                title=(description or prompt)[:120],
            )
        except Exception as exc:  # noqa: BLE001 — typed failure to the model
            logger.exception("[subagent_task] background task insert failed")
            return self._failed(f"task_create_failed: {exc!s:.120}")
        if not row:
            return self._failed("task_create_failed")

        task_id = str(row["id"])
        await self._emit_parent(
            "subagent_spawned",
            {
                "child_run_id": None,
                "task_id": task_id,
                "mode": "async",
                "subagent_type": slug,
                "description": description or "",
                "continued_from": child_run_id,
            },
        )
        return {
            **self._failed(""),
            "status": "queued",
            "task_id": task_id,
            "error": None,
        }

    async def _resolve_agent_id(self, slug: str) -> Optional[UUID]:
        from app.repositories.agent_repository import get_agent_repository

        try:
            row = await get_agent_repository().get_by_slug(slug)
        except Exception:  # noqa: BLE001 — an unresolvable slug is a refusal
            logger.exception("[subagent_task] agent lookup failed slug={}", slug)
            return None
        return UUID(row["id"]) if row else None

    async def _emit_parent(self, event_type: str, payload: dict[str, Any]) -> None:
        """Put a sub-agent event on the PARENT's transcript. No recorder (CLI
        spawn, probe run) → nothing to write; ``events.emit`` is already
        best-effort, so telemetry can never fail a turn."""
        if self.parent_recorder is None:
            logger.debug(
                "[subagent_task] no parent recorder; {} not recorded", event_type
            )
            return
        from app.services.ai.runner.events import emit

        await emit(self.parent_recorder, event_type, payload)

    # ── continue (child_run_id) ───────────────────────────────────────

    async def _child_chain_ok(self, child_run_id: str) -> bool:
        """True when ``child_run_id`` is this caller's to continue.

        The id comes from the model, so this is the guard that keeps
        ``child_run_id`` from reading a stranger's transcript. TWO arms, and
        a child that satisfies either is ours:

        * **ancestry** — walk up ``parent_run_id`` / ``fork_of_run_id`` (a
          continued round hangs off the round before it, not off the parent)
          and stop at ``MAX_PARENT_HOPS`` so a data cycle cannot spin forever;
        * **same issue** — the child's ``issue_id`` equals the issue this turn
          belongs to.

        The second arm is not a loosening for convenience: a BACKGROUND child
        returns after the turn that spawned it has ended, so the only natural
        way to continue it is the issue's next turn — a SIBLING of the spawner,
        which the walk can never reach. Before Task 7b every such attempt was
        refused with ``not_your_child`` (2026-09-10 acceptance: child hanging
        off turn 1, request arriving on turn 2), which made background
        continuation impossible rather than merely awkward.

        A conversation-scoped run has no issue, so it keeps the ancestor rule
        alone; a child whose own ``issue_id`` is NULL is an unanswered
        question, not a match — adopting it would hand every unscoped run's
        transcript to whichever issue asked first.
        """
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import AgentRuns

        target = _as_int(self.active_parent_run_id)
        my_issue = _as_int(self.active_issue_id)
        if target is None and my_issue is None:
            # Neither arm has anything to compare. Falling through would run
            # the walk with ``target is None``, and a child whose
            # ``parent_run_id`` is NULL would then match it.
            return False
        cursor = _as_int(child_run_id)
        if cursor is None:
            return False

        try:
            async with read_scope() as session:
                for hop in range(MAX_PARENT_HOPS):
                    row = (
                        await session.execute(
                            select(
                                AgentRuns.parent_run_id,
                                AgentRuns.fork_of_run_id,
                                AgentRuns.issue_id,
                            )
                            .where(AgentRuns.id == cursor)
                            .limit(1)
                        )
                    ).first()
                    if row is None:
                        return False
                    parent, forked_from = row[0], row[1]
                    if hop == 0 and my_issue is not None:
                        # The child's OWN row answers the issue arm, so it is
                        # settled before a single hop is spent on it.
                        child_issue = _as_int(row[2])
                        if child_issue is not None and child_issue == my_issue:
                            return True
                    if target is not None and (
                        parent == target or forked_from == target
                    ):
                        return True
                    cursor = parent or forked_from
                    if cursor is None:
                        return False
        except Exception:  # noqa: BLE001 — an unverifiable claim is refused
            logger.exception(
                "[subagent_task] child chain check failed child_run_id={}",
                child_run_id,
            )
            return False
        return False

    async def _continue_messages(
        self, child_run_id: str, prompt: str
    ) -> tuple[list[dict[str, Any]], int]:
        """The child's own history, then the new instruction."""
        from app.services.ai.runner.replay import events_upto, messages_from_events

        events, last_seq = await self._load_child_events(child_run_id)
        msgs = messages_from_events(events_upto(events, last_seq))
        return ([*msgs, {"role": "user", "content": prompt}], last_seq)

    async def _load_child_events(
        self, child_run_id: str
    ) -> tuple[list[dict[str, Any]], int]:
        """``(rows, max_seq)`` in seq order. An unreadable transcript yields
        an empty history rather than raising: the continued turn then reads
        as a fresh one, which is degraded but not wrong."""
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import AgentRunTranscriptEvents

        try:
            async with read_scope() as session:
                rows = (
                    (
                        await session.execute(
                            select(
                                AgentRunTranscriptEvents.seq,
                                AgentRunTranscriptEvents.event_type,
                                AgentRunTranscriptEvents.payload,
                            )
                            .where(AgentRunTranscriptEvents.run_id == int(child_run_id))
                            .order_by(AgentRunTranscriptEvents.seq.asc())
                        )
                    )
                    .mappings()
                    .all()
                )
        except Exception:  # noqa: BLE001
            logger.exception(
                "[subagent_task] transcript read failed child_run_id={}", child_run_id
            )
            return ([], 0)
        events = [dict(r) for r in rows]
        return (events, int(events[-1]["seq"]) if events else 0)

    async def _load_child_metadata(self, child_run_id: str) -> dict[str, Any]:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import AgentRuns

        try:
            async with read_scope() as session:
                meta = (
                    await session.execute(
                        select(AgentRuns.metadata_json).where(
                            AgentRuns.id == int(child_run_id)
                        )
                    )
                ).scalar_one_or_none()
        except Exception:  # noqa: BLE001
            logger.exception(
                "[subagent_task] metadata read failed child_run_id={}", child_run_id
            )
            return {}
        return meta if isinstance(meta, dict) else {}

    async def _round_of(self, child_run_id: str) -> int:
        """Round number for the run that continues ``child_run_id``. A run
        recorded before rounds existed has none, and the turn continuing it
        is by definition the second."""
        meta = await self._load_child_metadata(child_run_id)
        try:
            return int(meta.get("round") or 1) + 1
        except (TypeError, ValueError):
            return 2

    @staticmethod
    def _build_envelope(
        *,
        result: dict[str, Any],
        sub_run_id: Any,
        recorder: Any = None,
    ) -> dict[str, Any]:
        """Map AgentRunner.run_turn's verbose result into the compact
        envelope the parent agent reads. Keep this small — extra
        fields have token cost in the parent's context."""
        content = result.get("content") or ""
        error = result.get("error")
        status = "failed" if error else "success"

        # Whole-sub-turn token total. The recorder accumulates prompt+completion
        # across ALL iterations and is the source of truth; run_turn's result
        # carries only the LAST iteration's usage under result['raw']['usage']
        # and NEVER a top-level 'usage' — so the previous `result['usage']` read
        # always yielded 0 and the parent's fan-out cost tree was blank.
        tokens_used = _tokens_of(recorder) if recorder is not None else 0
        if tokens_used == 0:
            usage = (result.get("raw") or {}).get("usage") or {}
            tokens_used = int(usage.get("total_tokens", 0) or 0)

        return {
            "summary": content,
            "key_findings": [],
            "files_created": [],
            "tokens_used": tokens_used,
            "sub_run_id": str(sub_run_id) if sub_run_id else None,
            "status": status,
            **({"error": error} if error else {}),
        }

    @staticmethod
    def _failed(error_msg: str) -> dict[str, Any]:
        """Standard envelope shape for the never-launched failure
        cases (depth, slug, lookup). Parent agent reads ``status`` and
        ``error`` to decide whether to retry / fall back."""
        return {
            "summary": "",
            "key_findings": [],
            "files_created": [],
            "tokens_used": 0,
            "sub_run_id": None,
            "status": "failed",
            "error": error_msg,
        }


__all__ = [
    "DEFAULT_MAX_PARALLEL",
    "ENVELOPE_KEYS",
    "MAX_DELEGATION_DEPTH",
    "MAX_FANOUT",
    "SubAgentTaskService",
]
