# MediaHub Backend Dockerfile
# Frontend is deployed separately to Vercel

FROM python:3.12-slim

# Install Chrome, build tools and dependencies
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    gnupg \
    chromium \
    chromium-driver \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    build-essential \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Set Chrome environment variables
ENV CHROME_PATH=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# Install uv package manager
RUN pip install uv

# Set working directory
WORKDIR /app

# Copy backend code
COPY backend/ .

# Remove any existing local venv (different architecture incompatible)
RUN rm -rf .venv

# Install Python dependencies
RUN uv sync

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Create downloads directory and set permissions
RUN mkdir -p /app/downloads && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Start command
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
