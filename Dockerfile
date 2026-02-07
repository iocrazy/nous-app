# MediaHub Backend Dockerfile
# Frontend is deployed separately to Vercel

# ============================================
# Stage 1: Build Rust mediahub-core module
# ============================================
FROM rust:1.84-slim AS rust-builder

RUN apt-get update && apt-get install -y \
    python3-dev \
    python3-pip \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages maturin

WORKDIR /rust
COPY mediahub-core/ .
RUN maturin build --release

# ============================================
# Stage 2: Final Python application
# ============================================
FROM python:3.12-slim

# Install Chrome, ffmpeg, build tools and dependencies
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    gnupg \
    chromium \
    chromium-driver \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    build-essential \
    ffmpeg \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Set Chrome environment variables
ENV CHROME_PATH=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# Install uv package manager
RUN pip install uv

# Install mediahub-core Rust module from build stage
COPY --from=rust-builder /rust/target/wheels/*.whl /tmp/
RUN pip install /tmp/mediahub_core*.whl && rm -f /tmp/mediahub_core*.whl

# Install yt-dlp and faster-whisper
RUN pip install yt-dlp==2024.12.23 faster-whisper==1.1.0

# Set working directory
WORKDIR /app

# Copy backend code
COPY backend/ .

# Remove any existing local venv (different architecture incompatible)
RUN rm -rf .venv

# Install Python dependencies
RUN uv sync

# Create downloads directory
RUN mkdir -p /app/downloads

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Start command
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
