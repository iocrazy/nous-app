# MediaHub Docker Deployment

This directory contains all files needed for Docker deployment.

## Quick Start

### 1. Configure Environment

```bash
cp .env.example .env
# Edit .env with your actual values
```

### 2. Start Services

```bash
docker-compose up -d
```

### 3. Check Status

```bash
docker-compose ps
docker-compose logs -f mediahub
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| mediahub | 8080 | Main API server |
| celery-worker | - | Background task executor |
| celery-beat | - | Scheduled task scheduler |
| flower | 5555 | Task monitoring UI |
| redis | 6379 | Message queue |

## File Structure

```
docker/
├── .env.example        # Environment template
├── .env                # Your configuration (not in git)
├── config.yml          # Business configuration
├── docker-compose.yml  # Main compose file
├── downloads/          # Downloaded media files
└── README.md           # This file
```

## Common Commands

```bash
# Start all services
docker-compose up -d

# Stop all services
docker-compose down

# View logs
docker-compose logs -f

# Restart a service
docker-compose restart mediahub

# Pull latest images
docker pull imheygo/mediahub:latest
docker-compose up -d --force-recreate

# Clean up
docker-compose down -v  # Remove volumes too
docker image prune -f   # Remove unused images
```

## Update Deployment

When new code is pushed to master:

```bash
cd /volume1/docker/mediahub/docker
git pull origin master
docker pull imheygo/mediahub:latest
docker-compose up -d --force-recreate mediahub celery-worker celery-beat
```

## Troubleshooting

### Check container logs
```bash
docker logs mediahub
docker logs mediahub-celery-worker
```

### Check container health
```bash
docker inspect mediahub | grep -A 10 Health
```

### Enter container shell
```bash
docker exec -it mediahub /bin/sh
```
