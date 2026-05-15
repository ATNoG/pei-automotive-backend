# Performance Analysis — pei-automotive-backend

## How to run the tests

### Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| k6   | ≥ 0.50  | `winget install k6` / [grafana.com/docs/k6](https://grafana.com/docs/k6/latest/set-up/install-k6/) |
| Python 3.10+ | — | project requirement |
| paho-mqtt | — | already in `requirements.txt` |

The full Docker Compose stack must be running before any test:

```bash
cd pei-automotive-backend
docker compose up --build -d
```

### k6 tests (HTTP / database-api)

```bash
# Baseline — /health, no auth, 20 VUs for 60 s
k6 run tests/performance/k6/health.js -e BASE_URL=http://localhost:8082

# Load — ramp to 50 VUs, GET + PATCH preferences, 7 min total
k6 run tests/performance/k6/load.js \
  -e BASE_URL=http://localhost:8082 \
  -e KC_URL=http://localhost:8081   \
  -e KC_USER=<username>             \
  -e KC_PASS=<password>

# Stress — ramp to 200 VUs, find breaking point, 13 min total
k6 run tests/performance/k6/stress.js <same -e flags>

# Spike — 10 → 200 → 10 VUs in 30 s, then 2 min recovery, 4.5 min total
k6 run tests/performance/k6/spike.js <same -e flags>
```

The Keycloak client must have **Direct Access Grants** enabled (ROPC flow).  
`KC_CLIENT` defaults to `automotive-app`; override with `-e KC_CLIENT=<id>`.

### MQTT tests

```bash
# Preset scenarios
python tests/performance/mqtt_load.py --scenario baseline
python tests/performance/mqtt_load.py --scenario load
python tests/performance/mqtt_load.py --scenario stress
python tests/performance/mqtt_load.py --scenario spike

# Custom
python tests/performance/mqtt_load.py \
  --host localhost --port 1884 \
  --publishers 20 --rate 100 --duration 120
```

---

## Database-API bottleneck map

### Request path (before optimizations)

| Step | Queries | Notes |
|------|---------|-------|
| JWT validation | 0 DB | PyJWKClient caches the JWKS in memory |
| `ensure_user_exists` | 1 UPSERT | `INSERT … ON CONFLICT DO UPDATE` on users PK |
| `get_or_create_defaults` (old) | 1–2 | SELECT; then INSERT if missing |
| `update` | 1 UPDATE | dynamic SET clause |
| **GET total** | **2–3** | |
| **PATCH total (old)** | **3–5** | extra `SELECT 1` + conditional `get_or_create` |

### Request path (after optimizations — this codebase)

| Step | Queries | Notes |
|------|---------|-------|
| JWT validation | 0 DB | |
| `ensure_user_exists` | 1 UPSERT | unchanged |
| `get_or_create_defaults` (new) | **1** | CTE: INSERT … ON CONFLICT DO NOTHING RETURNING ∪ SELECT |
| `update` | 1 UPDATE | unchanged |
| **GET total** | **2** | saved 1 round trip |
| **PATCH total** | **3** | saved 2 round trips |

### asyncpg connection pool

Default before: `min=10, max=10` — every VU beyond 10 queues on the pool.  
Default after: `min=5, max=20` — tunable via `DB_POOL_MIN` / `DB_POOL_MAX` env vars.

PostgreSQL 16 defaults to `max_connections=100`. Keep `DB_POOL_MAX × uvicorn_workers < 90`.

---

## Expected benchmark numbers (single-node Docker, laptop-grade hardware)

These are reference targets, not guaranteed values.

| Endpoint | Scenario | p50 | p95 | p99 |
|----------|----------|-----|-----|-----|
| `GET /health` | 20 VUs | < 5 ms | < 20 ms | < 50 ms |
| `GET /api/preferences/` | 30 VUs (load) | < 30 ms | < 100 ms | < 200 ms |
| `PATCH /api/preferences/` | 30 VUs (load) | < 40 ms | < 150 ms | < 300 ms |
| `GET /api/preferences/` | 200 VUs (stress) | < 200 ms | < 1 s | < 3 s |

---

## Bottleneck identification

### CPU saturation

**Symptom:** All latency percentiles rise together, no 5xx errors.  
**Tool:** `docker stats database-api` — watch CPU %.  
**Root cause:** Python GIL limits uvicorn throughput under CPU-bound work.  
Mitigation: `--workers N` in the uvicorn command (N = CPU cores).

### asyncpg pool exhausted

**Symptom:** Latency spikes sharply at a specific VU count, then plateaus.  
**Tool:** Add `EXPLAIN (ANALYZE, BUFFERS)` to slow queries; check pool wait time.  
**Root cause:** Pool `max_size` too low for concurrent VU count.  
Mitigation: Increase `DB_POOL_MAX`; if PostgreSQL `max_connections` is the ceiling, use PgBouncer.

### PostgreSQL connection limit

**Symptom:** `FATAL: remaining connection slots are reserved` in DB logs.  
**How to trigger:** >100 concurrent pool connections (default PG limit).  
Mitigation: Lower `DB_POOL_MAX`, add PgBouncer in transaction-pool mode.

### Keycloak JWKS latency

**Symptom:** `/health` is fast but `/api/preferences/` is slow even at low VU.  
**Root cause:** `PyJWKClient` caches JWKS but re-fetches on cache miss (every ~5 min).  
**Tool:** Add a `jwks_fetch` latency metric to the middleware; compare against DB latency.  
Mitigation: Use a longer `lifespan` or a background refresh thread for the JWKS cache.

### Mosquitto broker — MQTT

**Symptom in `mqtt_load.py`:** `loss_pct > 1%` or `p95 latency > 100 ms`.  
**Root cause options:**  
- `persistence true` in `mosquitto.conf` causes disk flushes — disable for performance.  
- QoS 0 drop on full receiver queue — broker drops unread messages.  
- Single-threaded Mosquitto — at very high rates (>10 000 msg/s) the broker saturates a single core.  
**Mitigation:** Use QoS 0 (already the case), disable persistence, or switch to EMQ X / HiveMQ for multi-core throughput.

### position_processor — Overpass API

**Symptom:** `d2p_api` latency in the pipeline monitor `> 500 ms`.  
**Root cause:** Cache miss forces an HTTP call to the Overpass API (external).  
**Tool:** Pipeline monitor `GET /api/snapshot` → `d2p` vs `d2p_api` — if `d2p_api.avg >> d2p.avg`, the cache miss rate is high.  
Mitigation: Tile cache is already in place; pre-warm with the test route set, or increase `CACHE_RADIUS` in `overpass_client.py`.

---

## Stress test failure modes

| VU count | Expected failure | What to check |
|----------|-----------------|---------------|
| > 20     | Pool queue latency spike | `asyncpg` pool `max_size` |
| > 100    | uvicorn request queue backpressure | uvicorn `--limit-concurrency` |
| > 200    | 502 / connection refused | OS `somaxconn`, ephemeral ports |
| > 100 DB connections | PostgreSQL `FATAL` | `max_connections` in `postgresql.conf` |

---

## Pipeline latency reference (from pipeline monitor)

| Metric | Description | Healthy range |
|--------|-------------|---------------|
| `d2p` | Ditto → position_processor publish | < 25 ms (cache hit), < 800 ms (cache miss) |
| `p2m` | Processor publish → monitor receive | < 5 ms (clock skew adjusted) |
| `u2a_<type>` | CarUpdate → alert detected | < 50 ms |
| `total` | Ditto event → alert published | < 1 s |
| `cache_rate` | Speed limit Overpass cache hit % | > 95% after warm-up |

These are observable live at `http://localhost:8765/api/snapshot` while the pipeline monitor is running.
