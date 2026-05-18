# Performance Analysis - pei-automotive-backend

## How to run the tests

### Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| k6   | >= 0.50  | `winget install k6` / [grafana.com/docs/k6](https://grafana.com/docs/k6/latest/set-up/install-k6/) |
| python 3.10+ | any | project requirement |
| paho-mqtt | any | already in `requirements.txt` |
| matplotlib | any | `pip install matplotlib` (required for `measure_latency.py` plots) |

The full Docker Compose stack must be running before any test:

```bash
cd pei-automotive-backend
docker compose up --build -d
```

### k6 tests (HTTP / database-api)

```bash
# Baseline: /health, no auth, 50 VUs for 60 s
k6 run tests/performance/k6/health.js -e BASE_URL=http://localhost:8082

# Load: ramp to 300 VUs, GET + PATCH preferences, 4 min total
k6 run tests/performance/k6/load.js `
  -e BASE_URL=http://localhost:8082 `
  -e KC_URL=http://localhost:8081 `
  -e KC_USER=driver `
  -e KC_PASS=driver123

# Stress: ramp to 1000 VUs, find breaking point, 5 min total
k6 run tests/performance/k6/stress.js `
  -e BASE_URL=http://localhost:8082 `
  -e KC_URL=http://localhost:8081 `
  -e KC_USER=driver `
  -e KC_PASS=driver123

# Spike: 20 -> 1000 -> 20 VUs in 45 s, then 2 min recovery, 4.5 min total
k6 run tests/performance/k6/spike.js `
  -e BASE_URL=http://localhost:8082 `
  -e KC_URL=http://localhost:8081  `
  -e KC_USER=driver `
  -e KC_PASS=driver123
```

The Keycloak client must have Direct Access Grants enabled (ROPC flow).
`KC_CLIENT` defaults to `automotive-app`; override with `-e KC_CLIENT=<id>`.

#### Results
```bash
█ THRESHOLDS 
  http_req_duration
  ✓ 'p(50)<5' p(50)=2.13ms
  ✓ 'p(95)<20' p(95)=4.8ms
  ✓ 'p(99)<50' p(99)=7.51ms
  http_req_failed
  ✓ 'rate<0.001' rate=0.00%

█ TOTAL RESULTS 
  checks_total.......: 169053 2815.137765/s
  checks_succeeded...: 99.94% 168967 out of 169053
  checks_failed......: 0.05%  86 out of 169053
  ✓ status 200
  ✓ body ok
  ✗ under 20 ms
    ↳  99% - ✓ 56265 / ✗ 86

HTTP
http_req_duration..............: avg=2.81ms  min=508.6µs med=2.13ms  max=422.56ms p(90)=3.79ms  p(95)=4.8ms  
  { expected_response:true }...: avg=2.81ms  min=508.6µs med=2.13ms  max=422.56ms p(90)=3.79ms  p(95)=4.8ms  
http_req_failed................: 0.00%  0 out of 56351
http_reqs......................: 56351  938.379255/s

EXECUTION
iteration_duration.............: avg=53.25ms min=50.52ms med=52.56ms max=473.07ms p(90)=54.34ms p(95)=55.23ms
iterations.....................: 56351  938.379255/s
vus............................: 50     min=50         max=50
vus_max........................: 50     min=50         max=5
NETWORK
data_received..................: 7.9 MB 131 kB/s
data_sent......................: 4.3 MB 71 kB/s
```

```bash
█ THRESHOLDS 
  http_req_duration
  ✓ 'p(95)<200' p(95)=62.64ms
  ✓ 'p(99)<500' p(99)=105.12ms
    {name:GET /api/preferences/}
    ✓ 'p(95)<200' p(95)=59.48ms
    {name:PATCH /api/preferences/}
    ✓ 'p(95)<300' p(95)=65.12ms
  http_req_failed
  ✓ 'rate<0.01' rate=0.00%


█ TOTAL RESULTS 
  checks_total.......: 136436 566.08639/s
  checks_succeeded...: 99.98% 136420 out of 136436
  checks_failed......: 0.01%  16 out of 136436
  ✓ token 200
  ✓ status 200
  ✓ has user_id
  ✗ under 200 ms
    ↳  99% - ✓ 27272 / ✗ 15
  ✗ under 300 ms
    ↳  99% - ✓ 27286 / ✗ 1

HTTP
http_req_duration....................: avg=23.14ms min=1.55ms med=17.12ms max=340.02ms p(90)=47.59ms p(95)=62.64ms
  { expected_response:true }.........: avg=23.14ms min=1.55ms med=17.12ms max=340.02ms p(90)=47.59ms p(95)=62.64ms
  { name:GET /api/preferences/ }.....: avg=21.72ms min=1.55ms med=15.93ms max=340.02ms p(90)=44.64ms p(95)=59.48ms
  { name:PATCH /api/preferences/ }...: avg=24.55ms min=2.94ms med=18.63ms max=321.73ms p(90)=50.25ms p(95)=65.12ms
http_req_failed......................: 0.00% 0 out of 54575
http_reqs............................: 54575 226.437045/s
EXECUTION
iteration_duration...................: avg=1.04s   min=1s     med=1.03s   max=1.41s    p(90)=1.09s   p(95)=1.11s  
iterations...........................: 27287 113.216448/s
vus..................................: 1     min=1          max=299
vus_max..............................: 300   min=300        max=300
NETWORK
data_received........................: 40 MB 164 kB/s
data_sent............................: 66 MB 272 kB/s
```

```bash
 █ THRESHOLDS 
  http_req_duration
  ✓ 'p(95)<2000' p(95)=1.37s
  http_req_failed
  ✓ 'rate<0.20' rate=0.85%

█ TOTAL RESULTS 
checks_total.......: 210375 697.562714/s
checks_succeeded...: 98.90% 208075 out of 210375
checks_failed......: 1.09%  2300 out of 210375
✓ token 200
✓ not 5xx
✗ not timeout
  ↳  97% - ✓ 102887 / ✗ 2300

HTTP
http_req_duration..............: avg=1.24s   min=2.13ms   med=64.49ms  max=1m0s   p(90)=791.13ms p(95)=1.37s
  { expected_response:true }...: avg=743.6ms min=2.13ms   med=62.86ms  max=57.98s p(90)=774.31ms p(95)=1.09s
http_req_failed................: 0.85%  900 out of 105361
http_reqs......................: 105361 349.35665/s
EXECUTION
iteration_duration.............: avg=1.45s   min=202.46ms med=265.29ms max=1m0s   p(90)=991.97ms p(95)=1.57s
iterations.....................: 105186 348.776383/s
vus............................: 13     min=0             max=1000
vus_max........................: 1000   min=1000          max=1000
NETWORK
data_received..................: 76 MB  253 kB/s
data_sent......................: 121 MB 400 kB/s
```

### Locust tests (HTTP / database-api - interactive web UI)

Locust is a python3-based alternative to k6 with a live browser dashboard.
Start it, open `http://localhost:8089`, set the user count and spawn rate,
and watch response time and RPS charts update in real time.

**Linux**:
```bash
# Install (once)
pip install locust

# Start the web UI - then open http://localhost:8089
KC_USER=driver KC_PASS=driver123 `
locust -f tests/performance/locustfile.py --host http://localhost:8082
```

**Windows**:
```bash
$env:KC_USER="driver"; $env:KC_PASS="driver123"; python3 -m locust -f tests/performance/locustfile.py --host http://localhost:8082
```

From the browser you can:
- Choose between `PreferencesUser` (authenticated GET + PATCH) and `HealthUser` (no-auth baseline)
- Set any number of users and spawn rate on the fly
- Start, stop, and reset the test without touching the terminal
- Watch live charts for response time, RPS, and failures

---

### MQTT tests

#### What mqtt_load measures

It simulates cars sending position updates to the broker and measures how long it takes for those messages to be received by a subscriber - the same role the Android app plays in production when it listens to `cars/updates`. The latency numbers (min / avg / median / p95 / p99 / max) are all in milliseconds and represent how fast the broker delivers a message from the moment it is sent.

This is useful because if the broker is slow under load, the Android app receives stale vehicle positions and alerts arrive late - directly hurting the driver experience. These numbers tell us whether the broker can keep up with the expected message volume.

#### Why received can be higher than sent

Each publisher marks a message as "sent" the moment it hands it off internally, before it actually reaches the broker. Because of this, a small number of messages are still in transit when the test ends - the subscriber catches them a fraction of a second later. A small negative loss like −0.64% is normal and not a real problem.

**Linux**:
```bash
python3 tests/performance/mqtt_load.py --scenario baseline
python3 tests/performance/mqtt_load.py --scenario load
python3 tests/performance/mqtt_load.py --scenario stress
python3 tests/performance/mqtt_load.py --scenario spike

# Custom
python3 tests/performance/mqtt_load.py \
  --host localhost --port 1884 \
  --publishers 20 --rate 100 --duration 120
```

**Windows**:
```bash
python3 tests/performance/mqtt_load.py --scenario baseline
python3 tests/performance/mqtt_load.py --scenario load
python3 tests/performance/mqtt_load.py --scenario stress
python3 tests/performance/mqtt_load.py --scenario spike

# Custom
python3 tests/performance/mqtt_load.py `
  --host localhost --port 1884 `
  --publishers 20 --rate 100 --duration 120
```

---

### Pipeline load test with stage breakdown

Simulates N cars concurrently injecting GPS via Ditto REST API (no Hono device registration required - car IDs are generated automatically). Measures per-stage latency and generates plots.

The three segments measured per injection:
- **Ditto→raw_updates** - Ditto WS event to ProximityFilter publish
- **raw_updates→updates** - ProximityFilter to PositionProcessor publish
- **E2E** - full pipeline (PUT sent to `cars/updates/<car_id>` received)

Install matplotlib once if not already installed:
```bash
pip install matplotlib
```
Run the test with desired parameters (e.g. 50 cars for 1 min at 1 update/s):
```bash
python3 tests/performance/measure_latency.py --cars 50 --duration 60
python3 tests/performance/measure_latency.py --cars 200 --duration 120 --rate 0.5
```

Plot is saved to `tests/performance/plots/`

---

## Database-API bottleneck map

### Request path (before optimizations)

| Step | Queries | Notes |
|------|---------|-------|
| JWT validation | 0 DB | PyJWKClient caches the JWKS in memory |
| `ensure_user_exists` | 1 UPSERT | INSERT ... ON CONFLICT DO UPDATE on users PK |
| `get_or_create_defaults` (old) | 1-2 | SELECT; then INSERT if missing |
| `update` | 1 UPDATE | dynamic SET clause |
| GET total | 2-3 | |
| PATCH total (old) | 3-5 | extra SELECT 1 + conditional get_or_create |

### Request path (after optimizations)

| Step | Queries | Notes |
|------|---------|-------|
| JWT validation | 0 DB | |
| `ensure_user_exists` | 1 UPSERT | unchanged |
| `get_or_create_defaults` (new) | 1 | CTE: INSERT ON CONFLICT DO NOTHING RETURNING + SELECT |
| `update` | 1 UPDATE | unchanged |
| GET total | 2 | saved 1 round trip |
| PATCH total | 3 | saved 2 round trips |

### asyncpg connection pool

Default before: `min=10, max=10` - every VU beyond 10 queues on the pool
Default after: `min=5, max=20` - tunable via `DB_POOL_MIN` / `DB_POOL_MAX` env vars
PostgreSQL 16 defaults to `max_connections=100`. Keep `DB_POOL_MAX x uvicorn_workers < 90`

---

## Bottleneck identification

### CPU saturation

Symptom: all latency percentiles rise together, no 5xx errors
Tool: `docker stats database-api` - watch CPU %
Root cause: python3 GIL limits uvicorn throughput under CPU-bound work
Mitigation: `--workers N` in the uvicorn command (N = CPU cores)

### PostgreSQL connection limit

Symptom: `FATAL: remaining connection slots are reserved` in DB logs
How to trigger: more than 100 concurrent pool connections (default PG limit)
Mitigation: lower `DB_POOL_MAX`, add PgBouncer in transaction-pool mode

---

## Pipeline latency reference (from pipeline monitor)

| Metric | Description | Healthy range |
|--------|-------------|---------------|
| `d2p` | Ditto to position_processor publish | < 25 ms (cache hit), < 800 ms (cache miss) |
| `p2m` | Processor publish to monitor receive | < 5 ms (clock skew adjusted) |
| `u2a_<type>` | CarUpdate to alert detected | < 50 ms |
| `total` | Ditto event to alert published | < 1 s |
| `cache_rate` | Speed limit Overpass cache hit % | > 95% after warm-up |

These are observable live at `http://localhost:8765/api/snapshot` while the pipeline monitor is running.
