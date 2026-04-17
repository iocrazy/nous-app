# Build stage
FROM node:20-alpine AS builder

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
