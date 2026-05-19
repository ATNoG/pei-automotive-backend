/**
 * Baseline test: GET /health, no auth
 * Establishes the absolute floor latency: just network + uvicorn + one Python return
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import { BASE_URL } from './config.js';

export const options = {
  scenarios: {
    baseline: {
      executor: 'constant-vus',
      vus: 50,
      duration: '60s',
    },
  },
  thresholds: {
    http_req_duration: ['p(50)<5', 'p(95)<20', 'p(99)<50'],
    http_req_failed:   ['rate<0.001'],
  },
};

export default function () {
  const res = http.get(`${BASE_URL}/health`);
  check(res, {
    'status 200':  (r) => r.status === 200,
    'body ok':     (r) => r.json('status') === 'ok',
    'under 20 ms': (r) => r.timings.duration < 20,
  });
  sleep(0.05);
}
