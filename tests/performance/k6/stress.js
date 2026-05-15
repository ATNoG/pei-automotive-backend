/**
 * Stress test: pushes VUs well beyond normal capacity to find the breaking point.
 *
 * Run:
 *   k6 run tests/performance/k6/stress.js \
 *     -e BASE_URL=http://localhost:8082 \
 *     -e KC_URL=http://localhost:8081   \
 *     -e KC_USER=<username>             \
 *     -e KC_PASS=<password>
 *
 * What to look for:
 *   - At which VU count does p95 latency cross 1 s?
 *   - What is the first error type: 502 (uvicorn backpressure), 503 (pool
 *     exhausted), or connection refused (OS socket limit)?
 *   - Does the system recover after VUs ramp down (resilience check)?
 *
 * Bottleneck indicators:
 *   - Latency spikes at ~20 VUs  -> asyncpg pool exhausted (max_size=20 default)
 *   - 5xx errors                  -> uvicorn worker queue full
 *   - Connection refused          -> OS ephemeral port exhaustion
 *   - CPU 100% on the DB host     -> query / index issue
 */
import http  from 'k6/http';
import { check, sleep } from 'k6';
import { BASE_URL } from './config.js';
import { fetchToken, getToken, bearer } from './utils.js';
import { KC_USER, KC_PASS } from './config.js';

export const options = {
  scenarios: {
    stress: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '2m', target: 50  },
        { duration: '3m', target: 100 },
        { duration: '3m', target: 150 },
        { duration: '3m', target: 200 },
        { duration: '2m', target: 0   },  // recovery
      ],
    },
  },
  thresholds: {
    // Relaxed; stress tests are designed to break things.
    http_req_duration: ['p(95)<2000'],
    http_req_failed:   ['rate<0.20'],
  },
};

export function setup() {
  return fetchToken(KC_USER, KC_PASS);
}

export default function (initial) {
  const token = getToken(initial);
  if (!token) return;

  const res = http.get(`${BASE_URL}/api/preferences/`, {
    headers: bearer(token),
    tags: { name: 'GET /api/preferences/' },
  });
  check(res, {
    'not 5xx':    (r) => r.status < 500,
    'not timeout': (r) => r.timings.duration < 10000,
  });
  sleep(0.2);
}
