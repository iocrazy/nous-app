# MediaHub Scripts

Utility scripts for development and deployment.

## Development Scripts

### start-dev.sh
Local development startup script for Mac.

```bash
# Check status
./scripts/start-dev.sh status

# Start all services
./scripts/start-dev.sh start

# Stop all services
./scripts/start-dev.sh stop

# View logs
./scripts/start-dev.sh logs
```

**Services managed:**
- Backend (uvicorn on port 8080)
- Frontend (vite on port 3000)
- Celery worker
- Redis

## Legacy Scripts

### deploy.sh
Old single-container Docker deployment (deprecated).
Use `docker/docker-compose.yml` instead.

### stop.sh
Stop Docker containers (legacy).

## Note

For production deployment, use the files in `docker/` directory:
```bash
cd docker
docker-compose up -d
```
