#!/bin/bash
# 停止 nous 应用容器
#
# 2026-07-26: 容器名 mediahub-* → nous-*。旧名在 gpupc 栈上根本不存在,
# 这个脚本一直在静默空转 —— `docker stop` 找不到容器只是报错退出。
# 同时把 frontend 换成 worker: 前端现在托管在 Cloudflare Pages,本地没有
# 对应容器,而 worker 才是需要跟 backend 一起停的那个。

echo "🛑 停止 nous..."
docker stop nous-backend 2>/dev/null || echo "  nous-backend 未运行"
docker stop nous-worker 2>/dev/null || echo "  nous-worker 未运行"
echo "✅ 已停止"
