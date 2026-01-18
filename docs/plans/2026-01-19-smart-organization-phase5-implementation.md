# Smart Organization Phase 5: Semantic Search

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement semantic search allowing users to find videos using natural language queries.

**Architecture:**
- Convert user query to embedding vector
- Search video_analysis table for similar embeddings
- Combine with metadata filtering (tags, date, author)
- Return ranked results with similarity scores

**Tech Stack:**
- OpenAI text-embedding-3-small for query embeddings
- pgvector for similarity search (cosine distance)
- FastAPI for search endpoints

---

## Task 5.1: Create Search Service

**Files:**
- Create: `backend/app/services/search_service.py`

**Description:**
Create a service that:
1. Converts natural language query to embedding
2. Searches for similar videos using vector similarity
3. Supports hybrid search (semantic + metadata filters)

---

## Task 5.2: Create Search API Router

**Files:**
- Create: `backend/app/api/search_router.py`
- Modify: `backend/app/api/__init__.py`
- Modify: `backend/app/main.py`

**Endpoints:**
- `POST /search/semantic` - Semantic search with natural language
- `POST /search/hybrid` - Combined semantic + filter search
- `GET /search/similar/{video_id}` - Find videos similar to given video

---

## Task 5.3: Create Search Schemas

**Files:**
- Create: `backend/app/schemas/search.py`

**Schemas:**
- SemanticSearchRequest
- HybridSearchRequest
- SearchResult
- SearchResponse

---

## Task 5.4: Add Search Statistics Function

**Files:**
- Create: `supabase/migrations/019_create_search_stats_function.sql`

**Description:**
Create database function to track search analytics and popular queries.

---

## Checkpoint: Phase 5 Complete

After completion:
- Semantic search via natural language
- Hybrid search with filters
- Similar video discovery
- Search analytics tracking

