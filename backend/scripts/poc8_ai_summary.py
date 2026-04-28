"""PoC #8 — DBOS workflow doing a real LLM summary call against NAS dev.

Workflow:
    step1 load_transcript  — fetch resource_transcripts.full_text via parsed_media.id
    step2 call_llm         — Doubao (OpenAI-compatible) summarize transcript
    step3 save_summary     — write parsed_media.ai_rewrite_text + ai_generated_at

Verifies:
    - end-to-end LLM workflow runs against NAS dev PG and writes a summary
    - step memoization: 2nd run with same workflow_id replays cached step outputs,
      does NOT re-call LLM (zero new completion tokens billed)

Run from backend/:
    uv run python scripts/poc8_ai_summary.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import psycopg
from openai import OpenAI

PARSED_MEDIA_ID = 297651070839545  # smallest transcript (6243 chars) in dev
USER_ID = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"  # owns doubao config in user_settings


def _load_dbos_dsn() -> str:
    env = Path(__file__).resolve().parent.parent / ".env.local"
    for line in env.read_text().splitlines():
        if line.startswith("DBOS_DATABASE_URL="):
            url = line.split("=", 1)[1]
            sep = "&" if "?" in url else "?"
            return f"{url}{sep}sslmode=disable"
    raise SystemExit("DBOS_DATABASE_URL missing from backend/.env.local")


def _admin_dsn() -> str:
    # supabase_admin via Supavisor — used for direct PG queries that bypass RLS
    return (
        "host=127.0.0.1 port=55433 dbname=postgres "
        "user=postgres.heygo-dev password=MediaHub_Dev_WtB2bzyMup1n0KY2P5gWoA "
        "sslmode=disable connect_timeout=8"
    )


def _load_doubao_config() -> dict:
    with psycopg.connect(_admin_dsn()) as conn:
        cur = conn.execute(
            "SELECT settings_json FROM user_settings WHERE user_id=%s", (USER_ID,)
        )
        row = cur.fetchone()
    if not row:
        raise SystemExit(f"no user_settings for {USER_ID}")
    settings = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    doubao = settings.get("ai_settings", {}).get("ai_providers", {}).get("doubao")
    if not doubao or not doubao.get("api_key"):
        raise SystemExit("doubao provider config not found")
    return doubao


# Track LLM invocations across DBOS recovery (helps assert memoization)
LLM_CALL_LOG = Path("/tmp/poc8_llm_calls.jsonl")


def _build_workflow():
    from dbos import DBOS, DBOSConfig

    cfg: DBOSConfig = {"name": "mediahub-poc8", "database_url": _load_dbos_dsn()}
    DBOS(config=cfg)

    @DBOS.step()
    def load_transcript(media_id: int) -> str:
        with psycopg.connect(_admin_dsn()) as conn:
            cur = conn.execute(
                """
                SELECT rt.full_text
                FROM parsed_media pm
                JOIN resources r ON r.media_id = pm.id
                JOIN resource_transcripts rt ON rt.resource_id = r.id
                WHERE pm.id = %s AND rt.full_text IS NOT NULL
                LIMIT 1
                """,
                (media_id,),
            )
            row = cur.fetchone()
        if not row:
            raise RuntimeError(f"no transcript for parsed_media={media_id}")
        text = row[0]
        print(f"[step1] loaded transcript: {len(text)} chars")
        return text

    @DBOS.step(retries_allowed=True, max_attempts=2)
    def call_llm(transcript: str, doubao_cfg: dict) -> str:
        with LLM_CALL_LOG.open("a") as fh:
            fh.write(json.dumps({"pid": os.getpid(), "ts": time.time(), "transcript_len": len(transcript)}) + "\n")
        client = OpenAI(api_key=doubao_cfg["api_key"], base_url=doubao_cfg["base_url"])
        model = doubao_cfg.get("selected_model") or "doubao-seed-2-0-pro-260215"
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是视频内容总结助手。给定视频转录文本，输出一段 80-120 字中文摘要，不要列表，不要emoji。"},
                {"role": "user", "content": transcript[:4000]},
            ],
            temperature=0.3,
            max_tokens=200,
        )
        summary = resp.choices[0].message.content.strip()
        print(f"[step2] LLM returned: {len(summary)} chars / model={model}")
        return summary

    @DBOS.step()
    def save_summary(media_id: int, summary: str) -> dict:
        with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
            conn.execute(
                "UPDATE parsed_media SET ai_rewrite_text=%s, ai_generated_at=now() WHERE id=%s",
                (summary, media_id),
            )
        print(f"[step3] saved summary to parsed_media id={media_id}")
        return {"media_id": media_id, "summary_len": len(summary)}

    @DBOS.workflow()
    def ai_summary_workflow(media_id: int, doubao_cfg: dict) -> dict:
        transcript = load_transcript(media_id)
        summary = call_llm(transcript, doubao_cfg)
        return save_summary(media_id, summary)

    return ai_summary_workflow


def _restore_baseline() -> None:
    """Reset parsed_media.ai_rewrite_text so each run starts clean."""
    with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
        conn.execute(
            "UPDATE parsed_media SET ai_rewrite_text=NULL, ai_generated_at=NULL WHERE id=%s",
            (PARSED_MEDIA_ID,),
        )


def _llm_call_count() -> int:
    if not LLM_CALL_LOG.exists():
        return 0
    return sum(1 for _ in LLM_CALL_LOG.read_text().splitlines() if _.strip())


def main() -> int:
    LLM_CALL_LOG.unlink(missing_ok=True)
    _restore_baseline()
    print(f"=== PoC #8 ai_summary against parsed_media id={PARSED_MEDIA_ID} ===")

    doubao_cfg = _load_doubao_config()
    print(f"loaded doubao config: model={doubao_cfg.get('selected_model')} key_len={len(doubao_cfg.get('api_key',''))}")

    from dbos import DBOS, SetWorkflowID

    workflow = _build_workflow()
    DBOS.launch()

    workflow_id = f"poc8-summary-{int(time.time())}"
    print(f"\n--- Run 1 (fresh): workflow_id={workflow_id} ---")
    with SetWorkflowID(workflow_id):
        result1 = workflow(PARSED_MEDIA_ID, doubao_cfg)
    calls_after_1 = _llm_call_count()
    print(f"run1 result: {result1}")
    print(f"LLM calls so far: {calls_after_1}")

    print(f"\n--- Run 2 (replay same workflow_id, expect memoization) ---")
    with SetWorkflowID(workflow_id):
        result2 = workflow(PARSED_MEDIA_ID, doubao_cfg)
    calls_after_2 = _llm_call_count()
    print(f"run2 result: {result2}")
    print(f"LLM calls so far: {calls_after_2}")

    # Verify summary persisted
    with psycopg.connect(_admin_dsn()) as conn:
        cur = conn.execute(
            "SELECT length(ai_rewrite_text), ai_rewrite_text FROM parsed_media WHERE id=%s",
            (PARSED_MEDIA_ID,),
        )
        slen, summary_preview = cur.fetchone()

    print(f"\n=== verification ===")
    print(f"parsed_media.ai_rewrite_text length: {slen}")
    print(f"summary preview: {summary_preview[:120] if summary_preview else None}...")
    print(f"results equal: {result1 == result2}")
    print(f"total LLM API calls: {calls_after_2} (expect 1 — second run should hit DBOS memoization)")

    fails = []
    if not slen or slen < 30:
        fails.append("summary not saved or too short")
    if calls_after_2 != 1:
        fails.append(f"expected exactly 1 LLM call, got {calls_after_2} — memoization broken")
    if result1 != result2:
        fails.append("run1 != run2 — workflow output not deterministic across replay")

    if fails:
        print("\nFAIL:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("\nPASS — DBOS workflow successfully ran real LLM call + memoized on replay")
    return 0


if __name__ == "__main__":
    sys.exit(main())
