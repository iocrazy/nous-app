# 部署链查阅资料

> 从 `CLAUDE.md` 的「CI/CD 部署」节迁出（2026-09-08）。那一节是每次会话都加载的，
> 而下面这些是**查得到就够用**的对照表和诊断流程 —— 放在这里，需要时再读。
>
> ⚠️ **所有「绝不做 X」类的禁令仍在 `CLAUDE.md`**，没有迁过来。安全约束不能放进
> 按需加载的内容里：万一没被读到就等于失效。

### 环境版本一览（怀疑"是不是被人偷偷改了"时先看这张表）

| 位置 | 版本 | 谁决定 |
|------|------|--------|
| `.python-version` | **3.13** | 我们（唯一真相，CI 三处都读它） |
| `backend/pyproject.toml` | **>=3.13** | 我们 |
| `nous-core/pyproject.toml` | **>=3.13** | 我们（pyo3 无 abi3，wheel 钉死 cp313） |
| `Dockerfile`（pin 的 digest） | **3.13**-slim | 我们 |
| `browser/pyproject.toml` | **3.12** | ⚠️ **上游** —— 见下 |
| `.nvmrc` | **22** | 我们（CI 与 `deploy-pages` 都读它） |
| `frontend/` `admin/` Dockerfile | node **22** | 我们 |

⚠️ **`browser/` 是 3.12，这是刻意的，不是漂移**：它的基础镜像是 `mcr.microsoft.com/playwright/python:v1.52.0-noble`，noble 自带 **Python 3.12.3**，版本由上游 playwright 镜像决定，而那个 tag 又必须跟 `dependencies` 里的 `playwright` pin 一起动。2026-08-07 曾把它"统一"成 `>=3.13`，结果 uv 找不到 3.13 就下载一个装进 **root 家目录**，而 Dockerfile 只 `chown /app` 后切 `USER pwuser` —— 容器起不来（`bad interpreter: Permission denied`），生产 smoke 拦下自动回滚。要真统一，是换基础镜像（自建 python:3.13 + 自装 chromium），不是改这一行。

⚠️ **gpupc 的系统 python 是 3.14**（`/usr/bin/python3`），跟本项目无关 —— uv 管的项目一律看 `.python-version`。但它会从 PATH 漏进构建：pyo3 的 build script 就是这么抓到 3.14 并报 "newer than PyO3's maximum supported version (3.13)" 的，所以 `ci.yml` 的 rust job 显式钉 `PYO3_PYTHON`。**诊断时别拿 `python3 -V` 当项目环境**。

### 红 CI 的诊断顺序（先读日志，再谈假设）

同一批红 CI 曾被连着误诊两次（先判"计费假红"、再判"要迁 self-hosted"），真相是第三种。**第一步永远是 `gh run view --job <id> --log-failed` 看首个 error**，再套下面的表：

| 首个 error | 含义 | 处置 |
|---|---|---|
| `runner_name` 为空 + `steps=0` + 2 秒 fail | 账户计费失败（托管 runner 被拦） | 临时走 self-hosted（不计费）。⚠️ 别指望"切 public"，见下。**2026-09-07 实测计费已恢复**，托管 runner 正常 |
| `Failed to resolve action download info: Service Unavailable` | **GitHub Actions 侧 outage**，job 死在准备阶段 | 只能等 + 重跑。**迁 self-hosted 无效** —— runner 一样要向 GitHub API 取 action 元数据 |
| 有真实步骤日志与耗时 | 代码/配置真的挂了 | 正常修 |

中间那档最容易误判成前一档：两者都是"一行业务代码没跑"，但一个是计费、一个是 GitHub 故障，处置**完全相反**（一个换 runner 有用，一个换了也没用）。区别在**有没有真实耗时** —— 计费拦截 2 秒就死，outage 会重试到几分钟甚至十几分钟。

⚠️ **「切 public 就能解」已被推翻**（2026-08-06）：repo 当时**已经是 public**，托管 runner 仍被全部拦下。那条旧经验（2026-07-26）适用的是**免费额度用尽**触发的强制回退；付款方式本身失败时公私有无关。所以判断顺序是先 `gh repo view --json visibility` 确认可见性，**如果已经是 public 还被拦，就不是额度问题，只能换 runner 或修账单**。

### 已退役的 NAS 老线（⚠️ 扳手当前是坏的）

`deploy-backend.yml`（ACR + watchtower → `mediahub-app-backend/worker`）与 `deploy-admin.yml` 已去掉 push 自动触发，只留 `workflow_dispatch`。

⚠️ **2026-07-26 起 `gh workflow run deploy-backend.yml` 已不能真正部署**，别把它当可用的回滚扳手。它在两个层面都断了：

1. **落地端不存在**：NAS 老栈已整体拆除，`mediahub-app-backend` / `worker` 连 `docker ps -a` 里都没有了。
2. **触发链已关闭**：nas-A 的 Watchtower HTTP API（token + 8083 端口）已整块移除，轮询改 24h，且没有任何容器带 `watchtower.enable` 标签（日志每轮 `Scanned=0`）。workflow 里那步 "Trigger Watchtower update" 现在必然打空，而它的兜底提示"will auto-poll in 5min"是错的。

起因：仓库切 public 后，旧版 `scripts/deploy.sh` 里硬编码的 `WATCHTOWER_TOKEN` 变成世界可读（git 历史永久）。只删 token 而保留 `HTTP_API_UPDATE=true` 会留下无鉴权端点，所以整条路径拆掉。

保留这两个 workflow 只是为了将来真要恢复 NAS 双轨时不用从零重写。恢复步骤见 [`docs/runbook/watchtower-config.md`](docs/runbook/watchtower-config.md) 的「若将来要恢复 NAS 作为回滚锚点」。

⚠️ 若真要恢复双轨，注意两边连的是**不同的 Supabase**，`backend/**` 一次改动会同时部署到两套互不相干的数据库；且 NAS supabase 容器 force-recreate 会让烙在容器里的 legacy JWT key 失效。

**当前真正的回滚手段**是 `deploy-gpu.yml` 的 smoke 失败自动回滚（`nous-backend:rollback` 镜像），见上方「后端链的关键设计」。

### 容器的配置来源不止一处（查"改了为什么没生效"时先看这张表）

```bash
docker inspect <容器> --format '{{index .Config.Labels "com.docker.compose.project"}} | {{index .Config.Labels "com.docker.compose.project.config_files"}}'
```

2026-08-22 实测：

| 容器 | 项目 | 配置来自 | 漂移风险 |
|---|---|---|---|
| `nous-db` 及整个 supabase 栈 | `mediahub-sb-prod` | datahub 活目录（不在 git） | 有，靠上面的 drift 检查兜 |
| `nous-backend` / `worker` / `browser` / `gateway` | `gpu-server` | **runner 工作区 checkout** | 无——每次部署从 git 重出 |
| `nous-admin` | `gpu-server` | **runner 工作区 checkout**（2026-09-07 起） | 无——每次部署从 git 重出 |

`nous-admin` 曾经是从**开发工作树**构建的，那是个真缺口：那棵树可以挂在任意分支上，而它与另外四个容器共用项目名 `gpu-server` 却指向不同的 compose 文件，从开发树 `up` 有可能顺带影响生产容器。2026-09-07 已接进 `deploy-gpu.yml`（paths / 回滚锚点 / `up.sh --build` 清单 / smoke 探针 / 回滚清单五处同改），与另外四个容器同源。
