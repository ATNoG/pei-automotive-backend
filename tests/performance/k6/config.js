export const BASE_URL   = __ENV.BASE_URL   || 'http://localhost:8082';
export const KC_URL     = __ENV.KC_URL     || 'http://localhost:8081';
export const KC_REALM   = __ENV.KC_REALM   || 'automotive-app';
export const KC_CLIENT  = __ENV.KC_CLIENT  || 'automotive-app';
export const KC_USER    = __ENV.KC_USER    || '';
export const KC_PASS    = __ENV.KC_PASS    || '';

// Default SLA thresholds — individual scripts can override.
export const THRESHOLDS = {
  http_req_duration: ['p(95)<200', 'p(99)<500'],
  http_req_failed:   ['rate<0.01'],
};
