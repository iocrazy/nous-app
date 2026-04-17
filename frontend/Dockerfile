# Build stage
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Build args for environment variables
ARG VITE_API_URL=""
ARG VITE_SUPABASE_URL=""
ARG VITE_SUPABASE_ANON_KEY=""

# Build the app
RUN npm run build

# Production stage — unprivileged nginx image runs as uid 101 by default
FROM nginxinc/nginx-unprivileged:alpine

# Copy built files
COPY --chown=nginx:nginx --from=builder /app/dist /usr/share/nginx/html

# Copy nginx config (server must listen on 8080 — unprivileged can't bind 80)
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
