#!/usr/bin/env python3
"""
Delete one or more provisioned cars from Ditto and Hono.

Usage:
    # delete specific cars (uses local metadata)
    python simulations/delete_car.py my-car another-car

    # delete all cars registered in simulations/devices/
    python simulations/delete_car.py --all

    # query Ditto directly and delete every Thing in org.acme namespace
    python simulations/delete_car.py --remote

    # dry run (print what would be deleted without doing it)
    python simulations/delete_car.py --remote --dry-run

    # control parallelism (default: 10 workers)
    python simulations/delete_car.py --remote --workers 20

    # only delete Things inactive for at least 30 minutes (default: 5)
    python simulations/delete_car.py --remote --min-age 30

Each car removes:
  - Ditto Thing  (org.acme:<slug>)
  - Ditto Policy (org.acme:<slug>)
  - Hono Device  (<tenant>/<thing_id>)  [optional, skipped if not registered]
  - Local metadata file (simulations/devices/<slug>.json)  [when it exists]
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings()

load_dotenv()

DITTO_API = os.getenv("DITTO_API_URL", "").rstrip("/")
DITTO_AUTH = (os.getenv("DITTO_USER", ""), os.getenv("DITTO_PASS", ""))
HONO_API = os.getenv("HONO_API_URL", "").rstrip("/")
HONO_AUTH = (os.getenv("HONO_USER", ""), os.getenv("HONO_PASS", ""))
HONO_TENANT = os.getenv("HONO_TENANT", "")

REGISTRY_DIR = (Path(__file__).resolve().parent / "devices").resolve()
DITTO_NAMESPACE = "org.acme"

_print_lock = threading.Lock()


def _make_session(auth: tuple) -> requests.Session:
    session = requests.Session()
    session.auth = auth
    session.verify = False
    retry = Retry(total=5, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


_ditto_session: requests.Session | None = None
_hono_session: requests.Session | None = None


def _ditto() -> requests.Session:
    global _ditto_session
    if _ditto_session is None:
        _ditto_session = _make_session(DITTO_AUTH)
    return _ditto_session


def _hono() -> requests.Session:
    global _hono_session
    if _hono_session is None:
        _hono_session = _make_session(HONO_AUTH)
    return _hono_session


def _safe_print(*args):
    with _print_lock:
        print(*args)


def _delete(session: requests.Session, url: str, dry_run: bool, label: str, optional: bool = False) -> bool:
    if dry_run:
        _safe_print(f"  [dry-run] DELETE {url}")
        return True
    try:
        resp = session.delete(url, timeout=15)
    except requests.RequestException as e:
        _safe_print(f"  {label}: network error — {e}")
        return optional
    if resp.status_code in (200, 202, 204):
        return True
    if resp.status_code == 404:
        return True
    if optional:
        return True
    _safe_print(f"  {label}: FAILED {resp.status_code} {resp.text[:120]}")
    return False


def delete_thing_id(thing_id: str, hono_tenant: str, dry_run: bool) -> bool:
    ok = True
    ok &= _delete(_ditto(), f"{DITTO_API}/api/2/things/{thing_id}", dry_run, "Ditto Thing")
    ok &= _delete(_ditto(), f"{DITTO_API}/api/2/policies/{thing_id}", dry_run, "Ditto Policy")
    _delete(_hono(), f"{HONO_API}/v1/devices/{hono_tenant}/{thing_id}", dry_run, "Hono Device", optional=True)

    if not dry_run:
        slug = thing_id.split(":")[-1]
        meta_path = REGISTRY_DIR / f"{slug}.json"
        if meta_path.exists():
            try:
                meta_path.unlink()
            except Exception:
                pass

    return ok


def iter_remote_pages(min_age_seconds: int = 0):
    """Yield (page_ids, skipped_in_page) one Ditto search page at a time."""
    cursor = None
    page_size = 200
    session = _make_session(DITTO_AUTH)
    now = datetime.now(timezone.utc)

    while True:
        option = f"size({page_size})"
        if cursor:
            option += f",cursor({cursor})"
        params = {"namespaces": DITTO_NAMESPACE, "fields": "thingId,_modified", "option": option}
        try:
            resp = session.get(f"{DITTO_API}/api/2/search/things", params=params, timeout=20)
        except requests.RequestException as e:
            sys.exit(f"Ditto search failed: {e}")
        if resp.status_code != 200:
            sys.exit(f"Ditto search failed: {resp.status_code} {resp.text[:200]}")

        data = resp.json()
        page_ids = []
        skipped = 0
        for item in data.get("items", []):
            if min_age_seconds > 0:
                modified_str = item.get("_modified", "")
                if modified_str:
                    try:
                        modified = datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
                        if (now - modified).total_seconds() < min_age_seconds:
                            skipped += 1
                            continue
                    except ValueError:
                        pass
            page_ids.append(item["thingId"])

        yield page_ids, skipped

        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(0.5)


def _delete_batch(thing_ids: list[str], dry_run: bool, workers: int) -> list[str]:
    """Delete a list of thing_ids in parallel. Returns list of failed IDs."""
    failed = []
    done = 0
    total = len(thing_ids)

    def _task(thing_id: str) -> tuple[str, bool]:
        ok = delete_thing_id(thing_id, HONO_TENANT, dry_run)
        return thing_id, ok

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_task, tid): tid for tid in thing_ids}
            for future in as_completed(futures):
                thing_id, ok = future.result()
                done += 1
                if not ok:
                    failed.append(thing_id)
                with _print_lock:
                    status = "ok" if ok else "FAILED"
                    print(f"  [{done}/{total}] {thing_id.split(':')[-1]} — {status}")
    except KeyboardInterrupt:
        with _print_lock:
            print(f"\n[interrupted] {done}/{total} processed before stop.")

    return failed


def delete_car(slug: str, dry_run: bool) -> bool:
    meta_path = REGISTRY_DIR / f"{slug}.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        thing_id = meta["thing_id"]
        hono_tenant = meta.get("hono_tenant", HONO_TENANT)
    else:
        thing_id = f"{DITTO_NAMESPACE}:{slug}"
        hono_tenant = HONO_TENANT
    return delete_thing_id(thing_id, hono_tenant, dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete provisioned cars from Ditto and Hono.")
    parser.add_argument("car_names", nargs="*", metavar="CAR_NAME", help="car slugs to delete")
    parser.add_argument("--all", action="store_true", help="delete all cars in simulations/devices/")
    parser.add_argument("--remote", action="store_true", help="query Ditto and delete every Thing in the org.acme namespace")
    parser.add_argument("--dry-run", action="store_true", help="print what would be deleted without doing it")
    parser.add_argument("--workers", type=int, default=10, help="parallel delete workers (default: 10)")
    parser.add_argument("--min-age", type=int, default=5, metavar="MINUTES",
                        help="skip Things modified within this many minutes (default: 5, 0 = delete all)")
    args = parser.parse_args()

    if not DITTO_API:
        sys.exit("DITTO_API_URL is not set in .env")
    if not HONO_API:
        sys.exit("HONO_API_URL is not set in .env")

    flags = [args.remote, args.all, bool(args.car_names)]
    if sum(flags) > 1:
        sys.exit("error: --remote, --all, and car names are mutually exclusive")

    if args.dry_run:
        print("--- DRY RUN — nothing will be deleted ---\n")

    if args.remote:
        min_age_seconds = args.min_age * 60
        age_msg = f"inactive for >{args.min_age}m" if min_age_seconds else "all (no age filter)"
        print(f"Deleting Things in '{DITTO_NAMESPACE}' from {DITTO_API} [{age_msg}] (streaming page-by-page)...\n")
        failed = []
        total = 0
        total_skipped = 0
        for page_ids, page_skipped in iter_remote_pages(min_age_seconds):
            total_skipped += page_skipped
            if page_ids:
                total += len(page_ids)
                failed.extend(_delete_batch(page_ids, args.dry_run, args.workers))

    elif args.all:
        slugs = [p.stem for p in sorted(REGISTRY_DIR.glob("*.json"))]
        if not slugs:
            print("No cars found in simulations/devices/")
            return
        thing_ids = [f"{DITTO_NAMESPACE}:{s}" for s in slugs]
        failed = _delete_batch(thing_ids, args.dry_run, args.workers)
        total = len(slugs)

    elif args.car_names:
        thing_ids = [f"{DITTO_NAMESPACE}:{s}" for s in args.car_names]
        failed = _delete_batch(thing_ids, args.dry_run, args.workers)
        total = len(args.car_names)

    else:
        parser.print_help()
        sys.exit(1)

    print()
    if failed:
        print(f"Done. {total - len(failed)}/{total} deleted. Failed: {failed}")
        sys.exit(1)
    else:
        print(f"Done. {total}/{total} deleted.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
