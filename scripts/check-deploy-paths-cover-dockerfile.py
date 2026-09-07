#!/usr/bin/env python3
"""断言根 Dockerfile 里每一条 COPY 的源路径都被 deploy-gpu.yml 的 paths 覆盖。

为什么需要这个门禁
------------------
根 Dockerfile 的构建上下文是仓库根(compose 里 `context: ../..`),所以它能
COPY 到仓库任意位置。而 deploy-gpu.yml 用 `paths:` 决定哪些改动触发部署 ——
两份清单都是**显式**的,一旦不同步就出现「改了会进镜像的文件,却不触发部署」。

这类缺陷不报错、不红灯,只是新代码永远发不出去,已在本仓出现四次:
  2026-07-27  gateway 未进 up.sh 清单     → 公网 502 五分钟
  ——          browser 未进部署链          → 600 个用例从没跑过
  2026-09-07  admin  未进部署链           → 只能从开发工作树手动部署
  2026-09-07  tools/codex-daemon 未进 paths → 只改 daemon 的 commit 不部署(#2173)

⚠️ smoke 探针抓不到这一类:镜像是从触发部署的那个 checkout build 的,所以
"镜像里装的"和"checkout 里有的"必然一致。缺的是**触发**,不是**内容**。

browser/Dockerfile 不在检查范围内 —— 它的构建上下文是 browser/ 自身,
凡是能 COPY 到的都在 browser/** 里,结构上不可能漏。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
WORKFLOW = ROOT / ".github/workflows/deploy-gpu.yml"


def copy_sources(dockerfile_text: str) -> list[tuple[int, str]]:
    """返回 [(行号, 源路径)]。跳过 `--from=` 的阶段间拷贝(不读构建上下文)。"""
    out: list[tuple[int, str]] = []
    for lineno, raw in enumerate(dockerfile_text.splitlines(), 1):
        line = raw.strip()
        if not re.match(r"^COPY\s", line, re.I):
            continue
        tokens = line.split()[1:]
        if any(t.startswith("--from=") for t in tokens):
            continue  # 从上一个 build stage 拷贝,与构建上下文无关
        args = [t for t in tokens if not t.startswith("--")]
        if len(args) < 2:
            continue  # 没有 <src> <dest> 两段,形状不认识就跳过
        for src in args[:-1]:  # 最后一个是目的地
            out.append((lineno, src))
    return out


def workflow_paths(workflow_text: str) -> list[str]:
    """取 on.push.paths 下的清单。解析失败必须报错,不能静默返回空 —— 空清单
    会让下面每一条 COPY 都"未覆盖",把门禁自己的故障伪装成被测对象的故障。"""
    m = re.search(r"^on:.*?^\s+push:.*?^\s+paths:\s*$", workflow_text, re.S | re.M)
    if not m:
        raise SystemExit("::error::deploy-gpu.yml 里找不到 on.push.paths —— 门禁无法判定")
    rest = workflow_text[m.end():]
    paths: list[str] = []
    for line in rest.splitlines():
        s = line.strip()
        if s.startswith("#") or not s:
            continue
        pm = re.match(r"^-\s*'([^']+)'\s*$", s) or re.match(r'^-\s*"([^"]+)"\s*$', s)
        if pm:
            paths.append(pm.group(1))
            continue
        break  # 清单结束
    if not paths:
        raise SystemExit("::error::on.push.paths 解析出空清单 —— 门禁无法判定")
    return paths


def covered(src: str, patterns: list[str]) -> bool:
    s = src.rstrip("/")
    for p in patterns:
        if p.endswith("/**"):
            if s == p[:-3] or s.startswith(p[:-3] + "/"):
                return True
        elif p == s:
            return True
    return False


def check(dockerfile_text: str, workflow_text: str) -> list[str]:
    patterns = workflow_paths(workflow_text)
    problems = []
    for lineno, src in copy_sources(dockerfile_text):
        if not covered(src, patterns):
            problems.append(
                f"Dockerfile:{lineno} `COPY {src}` 未被 deploy-gpu.yml 的 paths 覆盖 —— "
                f"只改这个路径的 commit 不会触发部署"
            )
    return problems


def selftest() -> int:
    """突变测试:把一条已覆盖的 path 拿掉,门禁必须转红。否则它是恒真的摆设。"""
    dt = DOCKERFILE.read_text(encoding="utf-8")
    wt = WORKFLOW.read_text(encoding="utf-8")

    if check(dt, wt):
        print("❌ 自测失败:当前仓库本应干净,门禁却报了问题")
        return 1
    print("  ✅ 场景1 当前仓库:无问题(符合预期)")

    mutated = wt.replace("      - 'backend/**'\n", "", 1)
    if mutated == wt:
        print("❌ 自测失败:找不到 backend/** 这条 path,无法做突变")
        return 1
    if not check(dt, mutated):
        print("❌ 自测失败:拿掉 backend/** 后门禁仍判干净 —— 它是恒真的")
        return 1
    print("  ✅ 场景2 拿掉 backend/**:正确报错(证明不是恒真)")

    try:
        check(dt, "on:\n  push:\n    branches: [master]\n")
        print("❌ 自测失败:paths 缺失时应报错中止,却静默通过")
        return 1
    except SystemExit:
        print("  ✅ 场景3 workflow 无 paths:正确中止(不把自身故障伪装成被测对象故障)")

    print("自测通过")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    problems = check(
        DOCKERFILE.read_text(encoding="utf-8"),
        WORKFLOW.read_text(encoding="utf-8"),
    )
    if problems:
        for p in problems:
            print(f"::error::{p}")
        print(
            "\n修法:把该路径加进 .github/workflows/deploy-gpu.yml 的 on.push.paths。"
            "\n若它确实不该触发部署(例如只在构建期读、产物不进镜像),"
            "请在 Dockerfile 该行上方注释说明,并在本脚本加显式豁免。"
        )
        return 1
    print("✅ 根 Dockerfile 的每条 COPY 源路径都被 deploy-gpu.yml 的 paths 覆盖")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
