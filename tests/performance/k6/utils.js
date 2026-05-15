import http from 'k6/http';
import { check, sleep } from 'k6';
import { KC_URL, KC_REALM, KC_CLIENT, KC_USER, KC_PASS } from './config.js';

const TOKEN_ENDPOINT = `${KC_URL}/realms/${KC_REALM}/protocol/openid-connect/token`;

export function fetchToken(username, password) {
  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt > 0) sleep(attempt);  // 1 s, then 2 s backoff
    const res = http.post(
      TOKEN_ENDPOINT,
      { grant_type: 'password', client_id: KC_CLIENT, username, password, scope: 'openid' },
      { headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, tags: { name: 'token' } },
    );
    if (check(res, { 'token 200': (r) => r.status === 200 })) {
      const body = res.json();
      return {
        access_token:  body.access_token,
        refresh_token: body.refresh_token,
        expires_in:    body.expires_in,
      };
    }
    console.error(`Token fetch failed (${res.status}): ${res.body}`);
    if (res.status >= 400 && res.status < 500) break;
  }
  return null;
}

export function doRefresh(refreshTok) {
  const res = http.post(
    TOKEN_ENDPOINT,
    { grant_type: 'refresh_token', client_id: KC_CLIENT, refresh_token: refreshTok },
    { headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, tags: { name: 'token' } },
  );
  if (res.status !== 200) return null;
  const body = res.json();
  return {
    access_token:  body.access_token,
    refresh_token: body.refresh_token,
    expires_in:    body.expires_in,
  };
}

let _token      = null;
let _refresh    = null;
let _expiresAt  = 0;

export function getToken(initial) {
  const now = Date.now() / 1000;
  if (_token && now < _expiresAt - 30) return _token;

  let data;
  if (_refresh) {
    data = doRefresh(_refresh);
  } else {
    data = initial || fetchToken(KC_USER, KC_PASS);
  }

  if (!data) { _token = null; _refresh = null; return null; }

  _token     = data.access_token;
  _refresh   = data.refresh_token;
  _expiresAt = now + data.expires_in - Math.random() * 60;
  return _token;
}

export function bearer(token) {
  return { Authorization: `Bearer ${token}` };
}
