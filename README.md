# MediaHub - 抖音媒体分析系统

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18+-61DAFB.svg)](https://react.dev)
[![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-3ECF8E.svg)](https://supabase.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A modern Douyin (TikTok China) video analysis and download system built with FastAPI, React, and Supabase.

基于 FastAPI + React + Supabase 的抖音视频分析和下载系统。

## Features / 功能特性

### Core Features
- **Link Parser** - Parse Douyin share links to extract video/image metadata
- **Media Download** - Auto-download videos, images, covers, and audio
- **Multi-format Support** - Videos, image carousels, and image-text posts
- **Batch Processing** - Process multiple links simultaneously
- **Real-time Sync** - Supabase Realtime for instant data updates

### User Management
- **User Authentication** - Supabase Auth with email/password login
- **Per-user Data Isolation** - Each user can only access their own data
- **User Settings** - Customizable download paths per user
- **API Key Management** - Create API keys with scoped permissions

### UI Features
- **Multiple View Modes** - Grid, List, and Feed views for library
- **Media Preview** - Full-screen video/image preview with ESC to close
- **Dark Theme** - Modern dark UI with TailwindCSS
- **Responsive Design** - Works on desktop and mobile devices
- **Dashboard Analytics** - Visual statistics and activity charts

## Tech Stack / 技术栈

| Layer | Technology |
|-------|------------|
| **Backend** | FastAPI + Python 3.11+ |
| **Database** | Supabase (PostgreSQL) |
| **Authentication** | Supabase Auth + API Key |
| **Frontend** | React 18 + TypeScript + Vite |
| **Styling** | TailwindCSS |
| **State Management** | Zustand + React Query |
| **Browser Automation** | DrissionPage |
| **Package Manager** | uv (Python) + npm (Node.js) |

## Project Structure / 项目结构

```
mediahub/
├── backend/                        # Backend Service
│   ├── app/
│   │   ├── api/                   # API Routes
│   │   │   ├── supabase_auth_router.py     # Auth endpoints
│   │   │   ├── supabase_douyin_router.py   # Video endpoints
│   │   │   ├── api_key_router.py           # API key management
│   │   │   ├── user_settings_router.py     # User settings
│   │   │   └── frontend_config_router.py   # Frontend config (YAML)
│   │   ├── core/                  # Core Configuration
│   │   │   ├── config.py          # Config management
│   │   │   ├── deps.py            # Dependency injection
│   │   │   ├── enums.py           # Enum definitions
│   │   │   └── api_key_scopes.py  # Permission scopes
│   │   ├── db/                    # Database
│   │   │   └── supabase_client.py # Supabase client
│   │   ├── repositories/          # Data Access Layer
│   │   │   ├── supabase_douyin_repository.py
│   │   │   ├── api_key_repository.py
│   │   │   └── user_settings_repository.py
│   │   ├── schemas/               # Pydantic Models
│   │   │   ├── douyin.py
│   │   │   └── api_key.py
│   │   └── services/              # Business Logic
│   │       ├── douyin_analysis.py
│   │       ├── douyin_parser.py
│   │       ├── downloader.py
│   │       ├── supabase_auth_service.py
│   │       └── supabase_douyin_service.py
│   ├── config.yml                 # Business config
│   ├── frontend_config.yml        # Frontend config (Supabase credentials)
│   └── pyproject.toml             # Python dependencies
├── frontend/                       # Frontend Application
│   ├── App.tsx                    # Main application
│   ├── supabaseClient.ts          # Supabase client with dynamic init
│   ├── components/                # React Components
│   │   ├── MediaCard.tsx          # Media detail card
│   │   ├── CompactMediaCard.tsx   # Grid view card
│   │   ├── LibraryTable.tsx       # List view table
│   │   ├── LibraryFeed.tsx        # Feed view
│   │   ├── SettingsView.tsx       # Settings page
│   │   ├── AuthOverlay.tsx        # Login modal
│   │   └── LandingPage.tsx        # Landing page
│   ├── services/                  # API Services
│   │   ├── dataService.ts         # Data operations
│   │   └── parserService.ts       # Link parsing
│   ├── types/                     # TypeScript Types
│   │   └── index.ts
│   └── package.json               # Frontend dependencies
├── supabase/                       # Supabase Configuration
│   └── migrations/                # Database Migrations
│       ├── 001_initial_schema.sql
│       ├── 002_optimize_schema.sql
│       ├── 003_api_keys.sql
│       └── 008_create_user_settings_table.sql
└── docs/                           # Documentation
    └── plans/                     # Design documents
```

## Quick Start / 快速开始

### Prerequisites / 环境要求

- Python 3.11+
- Node.js 18+
- Chrome/Chromium browser
- [uv](https://github.com/astral-sh/uv) package manager

### 1. Clone Repository

```bash
git clone https://github.com/your-repo/mediahub.git
cd mediahub
```

### 2. Configure Supabase

1. Create a new project at [Supabase](https://supabase.com)
2. Get your project URL and API Keys (Settings > API)
3. Execute migration files in SQL Editor:
   ```
   supabase/migrations/001_initial_schema.sql
   supabase/migrations/002_optimize_schema.sql
   supabase/migrations/003_api_keys.sql
   supabase/migrations/008_create_user_settings_table.sql
   ```

### 3. Environment Variables

**Backend** (`backend/.env`):

```bash
# Supabase (Required)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# Download Path
NAS_BASE_PATH=/path/to/download/videos

# Server Config (Optional)
HOST=0.0.0.0
APP_PORT=8080
```

**Frontend** (`frontend/.env`):

```bash
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your-anon-key
VITE_API_URL=http://localhost:8080
```

> **Note**: Supabase credentials can also be configured via the Settings page in the UI, which saves to `backend/frontend_config.yml`. Priority: YAML config > .env

### 4. Start Backend

```bash
cd backend
uv sync                                    # Install dependencies
uv run uvicorn app.main:app --reload       # Start dev server
```

API Documentation: http://localhost:8080/docs

### 5. Start Frontend

```bash
cd frontend
npm install                                # Install dependencies
npm run dev                                # Start dev server
```

Frontend UI: http://localhost:5173

---

## Docker Deployment / Docker 部署

### Quick Start with Docker

```bash
# Clone repository
git clone https://github.com/your-repo/mediahub.git
cd mediahub

# Configure environment
cp backend/.env.example backend/.env
# Edit backend/.env with your Supabase credentials

# Build and run
docker-compose up -d --build
```

Access: http://localhost:8080

### Docker Architecture

The Dockerfile uses multi-stage build to create a single container with both frontend and backend:

```
┌─────────────────────────────────────────────────┐
│              mediahub:latest                     │
├─────────────────────────────────────────────────┤
│  Frontend (static)  →  /app/static              │
│  Backend (FastAPI)  →  Port 8080                │
│  Chrome/Chromium    →  For DrissionPage         │
├─────────────────────────────────────────────────┤
│  Endpoints:                                      │
│  /           →  Frontend SPA                    │
│  /api/v1/*   →  Backend API                     │
│  /media/*    →  Downloaded media files          │
│  /docs       →  API Documentation               │
└─────────────────────────────────────────────────┘
```

### Volume Mounts

| Host Path | Container Path | Description |
|-----------|----------------|-------------|
| `./downloads` | `/app/downloads` | Downloaded videos/images |
| `./backend/.env` | `/app/.env` | Environment variables |
| `./backend/frontend_config.yml` | `/app/frontend_config.yml` | Frontend config (Supabase URL, download path) |

### docker-compose.yml

```yaml
version: '3.8'

services:
  mediahub:
    build: .
    image: mediahub:latest
    container_name: mediahub
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./downloads:/app/downloads                    # Media storage
      - ./backend/.env:/app/.env                      # Backend config
      - ./backend/frontend_config.yml:/app/frontend_config.yml  # Frontend config
    environment:
      - TZ=Asia/Shanghai
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
```

### Configuration

#### 1. Backend Environment (.env)

```bash
# Supabase (Required)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# Download path inside container (usually fixed)
NAS_BASE_PATH=/app/downloads
```

#### 2. Frontend Config (frontend_config.yml)

This file can be edited via the Settings page in the UI:

```yaml
supabase:
  url: "https://your-project.supabase.co"
  anon_key: "your-anon-key"

# Download path (must match volume mount)
default_download_path: "/app/downloads"
```

**Priority**: `frontend_config.yml` > `.env`

### Synology NAS Deployment

```bash
# SSH to NAS
ssh admin@your-nas-ip
sudo -i

# Create project directory
mkdir -p /volume1/docker/mediahub
cd /volume1/docker/mediahub

# Clone code
git clone https://github.com/your-repo/mediahub.git .

# Configure
cp backend/.env.example backend/.env
nano backend/.env  # Edit Supabase credentials

# Build and start
docker-compose up -d --build

# View logs
docker logs -f mediahub
```

### Custom Download Path

To save videos to a different location (e.g., NAS shared folder):

1. **Modify docker-compose.yml volume mount**:
   ```yaml
   volumes:
     - /volume1/video/douyin:/app/downloads  # Custom path
   ```

2. **Update frontend_config.yml**:
   ```yaml
   default_download_path: "/app/downloads"  # Keep container path
   ```

3. **Restart container**:
   ```bash
   docker-compose down && docker-compose up -d
   ```

### Manual Docker Commands

```bash
# Build image
docker build -t mediahub:latest .

# Run container
docker run -d \
    --name mediahub \
    --restart unless-stopped \
    -p 8080:8080 \
    -v $(pwd)/downloads:/app/downloads \
    -v $(pwd)/backend/.env:/app/.env \
    -v $(pwd)/backend/frontend_config.yml:/app/frontend_config.yml \
    -e TZ=Asia/Shanghai \
    mediahub:latest

# View logs
docker logs -f mediahub

# Stop
docker stop mediahub

# Remove
docker rm mediahub
```

### Troubleshooting

#### Build fails with memory error
```bash
docker build --memory=2g -t mediahub:latest .
```

#### Chrome/Chromium crashes
Add shared memory:
```bash
docker run --shm-size=1g ...
```

Or in docker-compose.yml:
```yaml
services:
  mediahub:
    shm_size: '1g'
```

#### Media files not loading (404)
1. Check `frontend_config.yml` has correct `default_download_path`
2. Ensure volume mount matches the path
3. Restart container after config changes

---

## API Documentation / API 文档

### Authentication Methods

The system supports two authentication methods:

| Method | Header | Format | Description |
|--------|--------|--------|-------------|
| **JWT Token** | `Authorization` | `Bearer <token>` | Obtained after user login, full permissions |
| **API Key** | `X-API-Key` | `dk_<secret>` | User-created, scoped permissions |

### API Endpoints

#### Authentication

| Endpoint | Method | Description | Auth |
|----------|--------|-------------|------|
| `/auth/signup` | POST | User registration | No |
| `/auth/signin` | POST | User login | No |
| `/auth/signout` | POST | User logout | Yes |
| `/auth/me` | GET | Get current user | Yes |
| `/auth/refresh` | POST | Refresh token | Yes |

#### Video Operations

| Endpoint | Method | Description | Scope |
|----------|--------|-------------|-------|
| `/douyin/fetch` | POST | Fetch single video | `douyin:fetch` |
| `/douyin/fetch/batch` | POST | Batch fetch videos | `douyin:fetch:batch` |
| `/douyin/videos` | GET | List videos | `douyin:videos:read` |
| `/douyin/videos/{aweme_id}` | GET | Get video details | `douyin:videos:read` |
| `/douyin/videos/{aweme_id}` | DELETE | Delete video | `douyin:videos:write` |
| `/douyin/videos/search` | POST | Search videos | `douyin:search` |
| `/douyin/statistics` | GET | Get statistics | `douyin:statistics` |
| `/douyin/retry/{aweme_id}` | POST | Retry download | `douyin:retry` |
| `/douyin/download/{aweme_id}` | GET | Download video file | `douyin:videos:read` |

#### User Settings

| Endpoint | Method | Description | Auth |
|----------|--------|-------------|------|
| `/settings` | GET | Get user settings | Yes |
| `/settings` | PUT | Update user settings | Yes |
| `/settings` | DELETE | Reset user settings | Yes |

#### Frontend Config (No Auth Required)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/config` | GET | Get frontend config (Supabase URL, Anon Key) |
| `/config` | PUT | Update frontend config |

#### API Key Management

| Endpoint | Method | Description | Auth |
|----------|--------|-------------|------|
| `/api-keys/scopes` | GET | Get available scopes | No |
| `/api-keys` | POST | Create API key | JWT |
| `/api-keys` | GET | List API keys | JWT |
| `/api-keys/{key_id}` | GET | Get key details | JWT |
| `/api-keys/{key_id}` | PATCH | Update key | JWT |
| `/api-keys/{key_id}` | DELETE | Delete key | JWT |
| `/api-keys/{key_id}/revoke` | POST | Revoke key | JWT |

### Permission Scopes

| Scope | Name | Description |
|-------|------|-------------|
| `douyin:fetch` | Fetch Video | Allow fetching single video via URL |
| `douyin:fetch:batch` | Batch Fetch | Allow batch fetching multiple videos |
| `douyin:videos:read` | Read Videos | Allow viewing video list and details |
| `douyin:videos:write` | Manage Videos | Allow deleting video records |
| `douyin:search` | Search Videos | Allow searching videos |
| `douyin:statistics` | View Statistics | Allow viewing statistics |
| `douyin:retry` | Retry Download | Allow retrying video download |
| `douyin:*` | All Permissions | All Douyin-related permissions |

---

## Database Schema / 数据库架构

### Tables

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  user_profiles  │     │  douyin_videos  │     │    authors      │
├─────────────────┤     ├─────────────────┤     ├─────────────────┤
│ id (UUID, PK)   │     │ id (BIGSERIAL)  │     │ id (BIGSERIAL)  │
│ username        │     │ aweme_id (UK)   │     │ author_id (UK)  │
│ avatar_url      │     │ video_title     │     │ nickname        │
│ role (enum)     │     │ author          │────▶│ avatar_url      │
│ created_at      │     │ author_id (FK)  │     │ follower_count  │
│ updated_at      │     │ aweme_type      │     │ signature       │
└─────────────────┘     │ video_desc      │     │ created_at      │
                        │ video_*_count   │     └─────────────────┘
                        │ cover_url       │
┌─────────────────┐     │ download_status │     ┌─────────────────┐
│  user_settings  │     │ user_id (FK)    │     │    api_keys     │
├─────────────────┤     │ created_at      │     ├─────────────────┤
│ id (UUID, PK)   │     └─────────────────┘     │ id (BIGSERIAL)  │
│ user_id (FK)    │                             │ key_id (UK)     │
│ download_path   │                             │ key_hash        │
│ settings_json   │                             │ key_prefix      │
│ created_at      │                             │ name            │
│ updated_at      │                             │ scopes (JSONB)  │
└─────────────────┘                             │ status (enum)   │
                                                │ user_id (FK)    │
                                                │ expires_at      │
                                                └─────────────────┘
```

### Enums

**Download Status (`video_download_status`)**:

| Value | Description | Color |
|-------|-------------|-------|
| `PENDING` | Pending download | Yellow |
| `PROCESSING` | Downloading | Blue |
| `COMPLETED` | Completed | Green |
| `FAILED` | Failed | Red |

**Video Type (`aweme_type`)**:

| Value | Description |
|-------|-------------|
| `0` | Standard video |
| `2` | Image carousel |
| `4` | Special video |
| `61` | Special video variant |
| `68` | Image-text post |

---

## Configuration / 配置说明

### Backend Configuration

**config.yml** - Business configuration:
- User agent strings
- CORS settings
- Timeout values
- Download paths

**frontend_config.yml** - Frontend config (editable via UI):
```yaml
supabase:
  url: "https://your-project.supabase.co"
  anon_key: "your-anon-key"
default_download_path: "/home/user/downloads/douyin"
```

### Configuration Priority

1. **Supabase Credentials**: `frontend_config.yml` > `.env`
2. **Download Path**: User settings (Supabase) > `frontend_config.yml` > Default
3. **Business Config**: `config.yml` > Environment variables > Defaults

---

## Development / 开发指南

### Backend Commands

```bash
cd backend
uv sync                                    # Sync dependencies
uv run uvicorn app.main:app --reload       # Start dev server
uv run pytest                              # Run tests
```

### Frontend Commands

```bash
cd frontend
npm install                                # Install dependencies
npm run dev                                # Start dev server
npm run build                              # Build for production
npm run lint                               # Lint code
npm run preview                            # Preview production build
```

### Adding New Features

1. Add Pydantic models in `backend/app/schemas/`
2. Add data access methods in `backend/app/repositories/`
3. Add business logic in `backend/app/services/`
4. Add API routes in `backend/app/api/`
5. Add API calls in `frontend/services/`
6. Add UI components in `frontend/components/`

---

## FAQ / 常见问题

### Q: Video fetch fails?

Check the following:
1. Ensure Chrome/Chromium browser is installed
2. Verify the Douyin link is valid (try opening in browser)
3. Check backend logs for detailed errors
4. Confirm network can access Douyin

### Q: How to use API Keys?

1. Log in to the frontend, go to "Settings" > "API Management"
2. Click "Create API Key", select required permissions
3. **Immediately copy and save the key** (shown only once!)
4. Use in API requests:
   ```bash
   curl -X GET http://localhost:8080/api/v1/douyin/videos \
     -H "X-API-Key: dk_your_secret_key"
   ```

### Q: Difference between JWT and API Key?

| Feature | JWT Token | API Key |
|---------|-----------|---------|
| Obtained via | User login | User creation |
| Validity | Short (needs refresh) | Customizable (permanent/expiry date) |
| Permissions | Full access | Limited by scopes |
| Use case | Frontend user interaction | Backend scripts/third-party integration |
| Header | `Authorization: Bearer <token>` | `X-API-Key: dk_xxx` |

### Q: How to configure Supabase without .env?

Use the Settings page in the UI to configure Supabase URL and Anon Key. These are saved to `backend/frontend_config.yml` and take priority over `.env` values.

---

## Changelog / 更新日志

### v2.2.0 (2026-01)

**New Features**:
- Frontend config API - Configure Supabase credentials via UI
- User settings persistence - Download path saved per user in Supabase
- File deletion - Delete local files when removing videos from library
- ListView improvements - ESC to close fullscreen, improved close button

**Changes**:
- `frontend_config.yml` for Supabase credentials (priority over .env)
- `user_settings` table for per-user settings
- Dynamic Supabase client reinitialization

### v2.1.0 (2026-01-13)

**New Features**:
- API key management system
- Dual authentication (JWT + API Key)
- Permission scopes control
- API key management UI
- Per-user data isolation

### v2.0.0 (2026-01)

**Breaking Changes**:
- Full migration to Supabase database
- Removed local SQLite support
- Supabase Auth replaces custom JWT authentication

**New Features**:
- `authors` table for author information
- `collections` for favorites functionality
- Video cover URL field

### v1.0.0

- Initial release
- SQLite local database support
- Basic video fetch and download

---

## License / 许可证

MIT License

Copyright (c) 2026

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
