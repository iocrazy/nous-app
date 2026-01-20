# Supabase Local Deployment Design

> **Status:** Design Complete
> **Created:** 2026-01-20
> **Author:** AI Assistant + heygo

---

## 1. Background

### 1.1 Current Architecture
- Supabase Cloud (hosted) with network latency ~700ms from China
- Backend: FastAPI + Celery + Redis
- Frontend: React + Vite
- Storage: Videos already on local NAS

### 1.2 Migration Goal
Migrate from Supabase Cloud to self-hosted Supabase on Synology NAS to reduce latency and gain full control.

---

## 2. Server Specifications

| Item | Specification |
|------|---------------|
| Model | Synology DS3622xs+ |
| CPU | Intel Xeon D-1531 (6 cores, 2.2GHz) |
| Memory | 64GB |
| Storage | NAS RAID |
| Network | Reverse proxy configured |

**Conclusion:** Resources are sufficient (estimated usage ~2.5GB / 64GB = ~4%)

---

## 3. Architecture Decision

### 3.1 Deployment Strategy: All-in-One Docker Compose ✅

```
┌─ docker-compose.yml ─────────────────────────────┐
│                                                  │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐            │
│  │ FastAPI │ │ Celery  │ │  Redis  │            │
│  └─────────┘ └─────────┘ └─────────┘            │
│                                                  │
│  ┌─────────────────────────────────────────┐    │
│  │ Supabase Stack                          │    │
│  │  - PostgreSQL (database)                │    │
│  │  - GoTrue (authentication)              │    │
│  │  - PostgREST (auto REST API)            │    │
│  │  - Realtime (subscriptions)             │    │
│  │  - Kong (API gateway)                   │    │
│  │  - Studio (admin dashboard)             │    │
│  └─────────────────────────────────────────┘    │
└──────────────────────────────────────────────────┘
```

**Why this approach:**
- Single `docker-compose up` to start everything
- Lowest network latency (all services on same host)
- Simplest configuration (Docker internal network)
- Easy maintenance and updates

**Alternatives considered:**
| Option | Decision | Reason |
|--------|----------|--------|
| Supabase on separate NAS | ❌ Rejected | Adds network latency, more complex |
| Only PostgreSQL on NAS | ❌ Rejected | Too complex, need to configure Auth separately |

---

## 4. Frontend/Backend Strategy

### 4.1 Decision: Merged Deployment ✅

```
┌─ Single Service ────────────────────────┐
│                                         │
│  FastAPI serves:                        │
│  ├─ /api/*  → Python backend handlers   │
│  └─ /*      → React static files        │
│                                         │
└─────────────────────────────────────────┘
```

**Benefits:**
- Single port exposed (8080)
- No CORS configuration needed
- Simpler deployment

**Note:** Separate deployment is possible later if needed for CDN.

---

## 5. Network Architecture

### 5.1 Current Setup

```
Internet Users
      ↓
https://mediahub.heygo.cn (Aliyun Reverse Proxy)
      ↓
https://mediahubserver.heygo.cn:88 (Synology Reverse Proxy)
      ↓
MediaHub Docker Services (Internal)
```

### 5.2 CDN Decision: Not Needed ❌

| Content Type | Cacheable | CDN Benefit |
|--------------|-----------|-------------|
| JS/CSS/Fonts | ✅ Yes | Minimal (already fast) |
| API Requests | ❌ No | None |
| Videos | ⚠️ Expensive | Not recommended |

**Reasoning:**
- Private/small team usage
- Aliyun reverse proxy already provides acceleration
- CDN adds cost and complexity
- Video CDN bandwidth costs would be high

**Future option:** If needed, deploy static files to Aliyun OSS + CDN.

---

## 6. Storage Architecture

### 6.1 Decision: Direct NAS Storage for Videos ✅

```
┌─ Data Storage Split ────────────────────────────┐
│                                                 │
│  PostgreSQL (Supabase)                          │
│  └─ Video metadata (title, author, path)        │
│                                                 │
│  NAS Disk (/volume1/mediahub/)                  │
│  ├─ /videos/  → Video files                     │
│  └─ /covers/  → Cover images                    │
│                                                 │
│  Database stores paths only:                    │
│  video_path: "/videos/abc123.mp4"               │
│  cover_path: "/covers/abc123.jpg"               │
└─────────────────────────────────────────────────┘
```

### 6.2 Why Not Supabase Storage?

| Aspect | NAS Disk | Supabase Storage (MinIO) |
|--------|----------|--------------------------|
| Speed | ✅ Direct file access | ⚠️ Extra API layer |
| Large files | ✅ Optimized | ⚠️ Not ideal for GB files |
| Backup | ✅ Synology Hyper Backup | ⚠️ Manual setup |
| Management | ✅ File Station | API only |

**Supabase Storage / S3 / MinIO Explanation:**

```
S3 = Amazon's object storage protocol (industry standard)

Supabase Storage architecture:
┌─────────────────────────────┐
│  REST API Layer             │
│         ↓                   │
│  MinIO (S3-compatible)      │  ← Local Supabase uses this
│         ↓                   │
│  Actual disk storage        │
└─────────────────────────────┘

Analogy:
- NAS Disk = Go to fridge yourself
- S3/Storage = Ask waiter to get it for you (extra step)
```

---

## 7. Supabase Components Reference

| Component | Purpose | Port (default) |
|-----------|---------|----------------|
| PostgreSQL | Database | 5432 |
| GoTrue | Authentication (login/signup/OAuth) | 9999 |
| PostgREST | Auto-generated REST API | 3000 |
| Realtime | WebSocket subscriptions | 4000 |
| Storage API | File storage (MinIO backend) | 5000 |
| Kong | API Gateway | 8000 |
| Studio | Admin Dashboard | 3000 |

---

## 8. Cloud vs Self-Hosted Comparison

| Feature | Self-Hosted | Supabase Cloud |
|---------|-------------|----------------|
| **Cost** | Free (own server) | Free tier + paid plans |
| **Extensions** | All available (pg_cron, pgvector) | Some need paid plan |
| **Data location** | Your server | AWS overseas |
| **Privacy** | Full control | Trust Supabase |
| **Maintenance** | Self-managed | Managed |
| **Multi-project** | Manual (multiple instances or schemas) | Built-in |
| **Latency** | Low (local) | ~700ms from China |

---

## 9. Multi-Tenancy Architecture

Current MediaHub uses multi-tenant architecture with shared Auth:

```
┌─ Single Supabase Instance ─────────────────────┐
│                                                │
│  auth schema (shared)                          │
│  └─ auth.users → All users                     │
│                                                │
│  public schema                                 │
│  ├─ teams → Team definitions                   │
│  ├─ team_members → User-team relationships     │
│  └─ douyin_videos → Videos (RLS by team)       │
│                                                │
│  RLS ensures:                                  │
│  - User A sees only Team A data                │
│  - User B sees only Team B data                │
└────────────────────────────────────────────────┘
```

**Multi-tenancy = One system serves multiple isolated customers**
- Each Team = One tenant
- Shared Auth allows users to join multiple teams
- RLS (Row Level Security) enforces data isolation

---

## 10. Resource Estimation

| Service | Memory (Idle) | Memory (Active) | CPU |
|---------|---------------|-----------------|-----|
| PostgreSQL | 200MB | 500MB+ | Low |
| GoTrue | 50MB | 100MB | Very Low |
| PostgREST | 50MB | 100MB | Low |
| Realtime | 100MB | 200MB | Low |
| Storage API | 50MB | 100MB | Low |
| Kong | 100MB | 200MB | Low |
| Studio | 150MB | 300MB | Low |
| FastAPI | 100MB | 300MB | Medium |
| Celery Worker | 150MB | 500MB | High (during tasks) |
| Redis | 50MB | 100MB | Very Low |
| **Total** | **~1GB** | **~2.5GB** | **2-4 cores** |

**Your server:** 6 cores / 64GB → More than sufficient ✅

---

## 11. Next Steps

1. [ ] Create docker-compose.yml with all services
2. [ ] Configure Supabase environment variables
3. [ ] Migrate database schema from cloud
4. [ ] Update frontend/backend to use local Supabase URL
5. [ ] Test authentication flow
6. [ ] Configure automatic backups
7. [ ] Set up pg_cron for cleanup tasks

---

## 12. Open Questions

- Backup strategy: Synology Hyper Backup vs pg_dump?
- SSL certificates for internal services?
- Monitoring and alerting setup?
