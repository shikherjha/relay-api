# relay-api

Relay BFF (FastAPI + Celery + Postgres/pgvector). Owns returns, disposition
orchestration, matching persistence, credits, and the LifeLedger client.
Consumes `relay-ml` (grading, `/embed`, `/wish-score`) and `relay-engine`
(disposition, rescue, matching) over HTTP. See `relay-dev/docs/plan.md`.

## Layout

```
app/
├── main.py            # FastAPI app + routers
├── worker.py          # Celery app (broker = Redis/SQS)
├── core/
│   ├── config.py      # pydantic-settings (env-driven)
│   └── carbon.py      # hard-coded CO2 constants (Impact Wallet + net-carbon gate)
├── db/
│   ├── base.py        # DeclarativeBase + model registry
│   └── session.py     # engine + SessionLocal + get_db
├── models/entities.py # all §6 tables (pgvector embeddings)
└── routers/health.py
alembic/               # migrations (0001 = full schema + pgvector)
```

## Run locally

```bash
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# bring up Postgres + Redis from relay-dev first:
#   cd ../relay-dev && docker compose up -d

alembic upgrade head            # create schema (extensions vector + pgcrypto, all tables)
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000/health` and `http://localhost:8000/docs`.

## Test

```bash
.venv\Scripts\python.exe -m pytest
```

## Environment

See `.env.example`. Compose injects `DATABASE_URL`, `REDIS_URL`,
`ML_SERVICE_URL`, `ENGINE_SERVICE_URL`. Secrets (AWS, LifeLedger key) stay in
`.env` and are never committed.

## Build order

Backend-first: schema (done — `0001_initial`) → endpoint design → high-level
flow → wire logic → UI last.
