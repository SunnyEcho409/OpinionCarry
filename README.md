# Opinion Carry Scanner

🟠 Made with love for OPINION LABS and the community. 🟠

Opinion Carry Scanner is a full-stack market monitoring tool for active Opinion markets.  
It combines a FastAPI backend with a React frontend to help users discover, filter, and act on opportunities faster.

## Why This Project Is Useful

- It gives a fast live view of active markets in one place.
- It helps users quickly locate markets by title, type, and outcome price percent.
- It highlights the exact matching outcome when a price filter is used.
- It makes incentive-based opportunities easier to isolate.
- It reduces data noise by using cached market snapshots and WebSocket-backed price updates.

## Project Structure

- `app/` - FastAPI backend, market collector, cache, refresh jobs, WS price stream integration.
- `frontend/` - React + TypeScript + Vite UI for filtering and browsing markets.
- `data/` - cached market data (`MARKETS_CACHE_FILE` target).

## Core Capabilities

- Active market listing with pagination.
- Search by title, market type, and child titles.
- Incentive-only toggle filter.
- Outcome percent filter (0-100%) with outcome-level highlighting.
- Auto-refresh loop for near real-time updates.
- Resilient price ingestion: WebSocket first, REST backfill when needed.

## Quick Start

### 1. Backend setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Run the API:

```bash
uvicorn app.main:app --reload
```

Backend default URL: `http://localhost:8000`

### 2. Frontend setup

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Frontend default URL: `http://localhost:5173`

If `VITE_API_BASE_URL` is empty, the frontend uses Vite proxy for `/markets` and `/health`.

## API Endpoints

- `GET /health`
- `GET /debug/refresh`
- `GET /markets?limit=500&offset=0&only_active=true`
- `GET /markets/all?limit=500&offset=0&only_active=true`
- `GET /markets/hold?limit=100&offset=0`

## Important Environment Variables

- `OPINION_API_KEY` - API key for Opinion OpenAPI access.
- `OPINION_HTTP_BASE` - base URL for Opinion OpenAPI.
- `OPINION_WS_URL` - WebSocket URL for live price stream.
- `REFRESH_INTERVAL_SEC` - refresh interval for hold scan logic.
- `MARKETS_REFRESH_INTERVAL_SEC` - periodic market cache refresh interval.
- `PRICE_REQUEST_RPS` - REST price backfill throttle.
- `MARKETS_CACHE_FILE` - local cache path persisted on disk.
- `VITE_API_BASE_URL` - frontend API base URL override.

## Data Flow (High Level)

1. Backend refresh jobs collect and cache market data.
2. Live prices are consumed from WebSocket (`market.last.price`).
3. Missing tokens are backfilled via REST (throttled).
4. Frontend polls backend and applies local UX filters and highlighting.

## Notes

- The backend is designed to avoid expensive per-request full rescans.
- Cache persistence allows warm startup with immediate data availability.
- UI and filtering logic are optimized for speed and decision-focused scanning.
