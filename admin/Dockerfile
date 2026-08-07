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
RUN npm ci

COPY . .
RUN npm run build

# Production stage — unprivileged nginx image runs as uid 101 by default
FROM nginxinc/nginx-unprivileged:alpine

COPY --chown=nginx:nginx --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
