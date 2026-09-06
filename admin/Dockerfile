# Build stage
# node 版本跟 .nvmrc 保持一致（22）。⚠️ 这个 Dockerfile 在出货路径上：
# nous-admin 是 compose 本机 build，且 admin 没有自动部署也不在 CI 覆盖内
# （见 CLAUDE.md「已知缺口」），所以改完只能靠手动 build 验证。
FROM node:22-alpine AS builder

WORKDIR /app

# Build-time args for Vite (baked into static JS)
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_ANON_KEY
ARG VITE_API_URL=""

COPY package*.json ./
# --mount=type=cache：npm 下载的包留在 builder 缓存里（2026-09-06 加，
# 构建机走 5G 计费网络）。层顺序本来就是对的 —— 先 COPY 清单再 npm ci，
# 最后才 COPY 源码，改代码不会重装依赖；缓存是再加一道保险，
# 让 package-lock.json 真变动时也只补下新增的包。
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY . .
RUN npm run build

# Production stage — unprivileged nginx image runs as uid 101 by default
FROM nginxinc/nginx-unprivileged:alpine

COPY --chown=nginx:nginx --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
