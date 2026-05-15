/**
 * Load test: steady ramp on GET + PATCH /api/preferences/.
 * Models normal-to-peak production traffic for the database-api service.
 *
 * Run:
 *   k6 run tests/performance/k6/load.js \
 *     -e BASE_URL=http://localhost:8082 \
 *     -e KC_URL=http://localhost:8081   \
 *     -e KC_USER=<username>             \
 *     -e KC_PASS=<password>
 *
 * What to watch:
 *   - http_req_duration{name:GET /api/preferences/}   should stay < 200 ms p95
 *   - http_req_duration{name:PATCH /api/preferences/} should stay < 300 ms p95
 *   - http_reqs                                        throughput (req/s)
 *   - asyncpg pool exhaustion shows as latency spikes at higher VUs
 */
import http  from 'k6/http';
import { check, group, sleep } from 'k6';
import { BASE_URL, THRESHOLDS } from './config.js';
import { fetchToken, getToken, bearer } from './utils.js';
import { KC_USER, KC_PASS } from './config.js';

export const options = {
  scenarios: {
    load: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '1m', target: 10  },  // warm up
        { duration: '3m', target: 30  },  // normal load
        { duration: '2m', target: 50  },  // peak load
        { duration: '1m', target: 0   },  // ramp down
      ],
    },
  },
  thresholds: {
    ...THRESHOLDS,
    'http_req_duration{name:GET /api/preferences/}':   ['p(95)<200'],
    'http_req_duration{name:PATCH /api/preferences/}': ['p(95)<300'],
  },
};

export function setup() {
  return fetchToken(KC_USER, KC_PASS);
}

export default function (initial) {
  const token = getToken(initial);
  if (!token) return;

  const hdrs = bearer(token);

  group('GET preferences', () => {
    const res = http.get(`${BASE_URL}/api/preferences/`, {
      headers: hdrs,
      tags: { name: 'GET /api/preferences/' },
    });
    check(res, {
      'status 200':   (r) => r.status === 200,
      'has user_id':  (r) => !!r.json('user_id'),
      'under 200 ms': (r) => r.timings.duration < 200,
    });
  });

  sleep(0.5);

  group('PATCH preferences', () => {
    const payload = JSON.stringify({
      appearance: { dark_mode: true, colorblind_enabled: false },
    });
    const res = http.patch(`${BASE_URL}/api/preferences/`, payload, {
      headers: { ...hdrs, 'Content-Type': 'application/json' },
      tags: { name: 'PATCH /api/preferences/' },
    });
    check(res, {
      'status 200':   (r) => r.status === 200,
      'under 300 ms': (r) => r.timings.duration < 300,
    });
  });

  sleep(0.5);
}
