/**
 * Stress test: pushes VUs well beyond normal capacity to find the breaking point
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
        { duration: '1m', target: 200  },
        { duration: '1m', target: 500  },
        { duration: '1m', target: 750  },
        { duration: '1m', target: 1000 },
        { duration: '1m', target: 0    },  // recovery
      ],
    },
  },
  thresholds: {
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
