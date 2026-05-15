/**
 * Spike test: simulates a sudden burst of traffic, then verifies recovery.
 *
 * Run:
 *   k6 run tests/performance/k6/spike.js \
 *     -e BASE_URL=http://localhost:8082 \
 *     -e KC_URL=http://localhost:8081   \
 *     -e KC_USER=<username>             \
 *     -e KC_PASS=<password>
 *
 * What to look for:
 *   - Does the error rate stay below 15% during the spike?
 *   - Does latency return to baseline within 60 s after the spike drops?
 *   - Does the service need a restart to recover (pool leak / deadlock)?
 *
 * Recovery is healthy if p95 goes back below 200 ms in the final 2-minute window.
 */
import http  from 'k6/http';
import { check, sleep } from 'k6';
import { BASE_URL } from './config.js';
import { fetchToken, getToken, bearer } from './utils.js';
import { KC_USER, KC_PASS } from './config.js';

export const options = {
  scenarios: {
    spike: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '1m',  target: 10  },  // baseline traffic
        { duration: '15s', target: 200 },  // sudden spike
        { duration: '1m',  target: 200 },  // sustain spike
        { duration: '15s', target: 10  },  // drop back to baseline
        { duration: '2m',  target: 10  },  // recovery window
        { duration: '30s', target: 0   },
      ],
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<3000'],
    http_req_failed:   ['rate<0.15'],
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
    'not 5xx':     (r) => r.status < 500,
    'not timeout': (r) => r.timings.duration < 10000,
  });
  sleep(0.1);
}
