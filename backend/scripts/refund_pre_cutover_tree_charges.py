# backend/scripts/refund_pre_cutover_tree_charges.py
"""一次性退款：把 2026-09-17 事故里追扣历史树的积分还回去。

事故机理、选谁、为什么幂等 —— 全在
``app/services/ai/billing/pre_cutover_refund.py`` 的模块 docstring 里。这个文件只是
入口。一句话版本：「树收口一次扣费」上线首轮，清扫器把 43 棵**上线前**的历史树
（``ended_at`` 在 09-10 ~ 09-15）收口并扣了 87 分，违反用户裁定「只向前不追扣」。

**默认 dry run。** 不带参数只读不写，把每一笔（team / root / points）与合计打出来；
真要退必须显式 ``--execute``。

本地::

    cd backend && uv run python scripts/refund_pre_cutover_tree_charges.py --dry-run
    cd backend && uv run python scripts/refund_pre_cutover_tree_charges.py --execute

生产 —— 跑**这个文件**，不要用 heredoc 另抄一份入口（会与这份漂移，而且没有任何
东西会发现）。``backend/`` 整个 COPY 进 ``/app``，``.dockerignore`` 不排除
``scripts/``::

    docker exec -w /app nous-backend /app/.venv/bin/python \\
        scripts/refund_pre_cutover_tree_charges.py --dry-run
    docker exec -w /app nous-backend /app/.venv/bin/python \\
        scripts/refund_pre_cutover_tree_charges.py --execute

⚠️ 这里不需要 ``-i``，正因为没有 stdin：CLAUDE.md 那条 ``-i`` 规矩针对的是
``docker exec ... python -`` 喂 heredoc，那种写法缺 ``-i`` 会静默丢内容。

重跑是安全的：退款经 ``rpc_refund_team_points_idempotent``（mig 123）的 partial
unique 索引，同一棵树退第二次是 no-op 并回报 ``already_refunded``。
"""

from __future__ import annotations

import argparse
import asyncio

from loguru import logger

from app.services.ai.billing.pre_cutover_refund import refund_pre_cutover_charges


async def _run(dry_run: bool) -> None:
    report = await refund_pre_cutover_charges(dry_run=dry_run)

    # 逐笔打出来 —— dry run 与实跑打的是同一份清单，好让人拿两次输出对照。
    for c in report.candidates:
        logger.info(
            "team={} root={} points={} charged_at={} root_started_at={}",
            c.team_id,
            c.root_run_id,
            c.points,
            c.charged_at,
            c.root_started_at,
        )
    for team_id, points in sorted(report.by_team.items()):
        logger.info("team {} 合计 {} 分", team_id, points)

    # ⚠️ 每个数说清它数的是什么。光看「refunded=0」分不出「没有要退的」和「每一笔
    # 都失败了」—— 那是两个相反的结论。
    logger.info(
        "{}：命中 {} 笔 / {} 分（{} 个 team）；本次退成 {} 笔 / {} 分，"
        "此前已退过 {} 笔，失败 {} 笔，改戳 {} 行",
        "dry run（一分钱都没动）" if dry_run else "done",
        len(report.candidates),
        report.points_total,
        len(report.by_team),
        report.refunded,
        report.points_refunded,
        report.already,
        report.failed,
        report.stamped,
    )
    if report.failed:
        logger.error(
            "有 {} 笔退款失败 —— 余额没动，原样重跑即可（幂等）", report.failed
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只读并打印清单，什么都不写（默认行为）",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="真的退款并改戳。不给这个标志一律 dry run",
    )
    args = parser.parse_args()
    if args.dry_run and args.execute:
        parser.error("--dry-run 与 --execute 不能同时给：说清楚这一次到底写不写")
    asyncio.run(_run(dry_run=not args.execute))


if __name__ == "__main__":
    main()
