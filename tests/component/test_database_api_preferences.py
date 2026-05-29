import asyncio
import os
import re
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pg8000
from testcontainers.postgres import PostgresContainer

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_API_DIR = ROOT_DIR / "src" / "services" / "database_api"
SCHEMA_PATH = ROOT_DIR / "db" / "init" / "02_schema.sql"

if str(DB_API_DIR) not in sys.path:
    sys.path.insert(0, str(DB_API_DIR))

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = object
asyncpg_stub.Connection = object
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)

# Minimal env needed for Settings() construction during imports.
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test_user")
os.environ.setdefault("DB_PASSWORD", "test_password")
os.environ.setdefault("KEYCLOAK_URL", "http://localhost:8080")
os.environ.setdefault("KEYCLOAK_REALM", "automotive-app")

from models.preference import UpdatePreferencesRequest
from repositories.preference_repository import PreferenceRepository
from routers import preferences as preferences_router


async def _ensure_user_exists_for_test(conn, user_id: UUID, username: str):
    await conn.execute(
        """
        INSERT INTO users (id, username)
        VALUES ($1, $2)
        ON CONFLICT (id) DO UPDATE
        SET username = EXCLUDED.username
        """,
        user_id,
        username,
    )


class PgConnectionAdapter:
    """Expose asyncpg-like async methods on top of a psycopg connection."""

    def __init__(self, conn):
        self._conn = conn

    @staticmethod
    def _prepare_query(query: str, args: tuple) -> tuple[str, tuple]:
        ordered_indexes: list[int] = []

        def repl(match: re.Match) -> str:
            ordered_indexes.append(int(match.group(1)) - 1)
            return "%s"

        sql = re.sub(r"\$(\d+)", repl, query)
        ordered_args = tuple(args[idx] for idx in ordered_indexes)
        return sql, ordered_args

    def _fetchrow_sync(self, query: str, args: tuple):
        sql, prepared_args = self._prepare_query(query, args)
        with self._conn.cursor() as cur:
            cur.execute(sql, prepared_args)
            row = cur.fetchone()
            if row is None:
                return None
            columns = [desc[0] for desc in cur.description]
            return dict(zip(columns, row))

    def _fetchval_sync(self, query: str, args: tuple):
        row = self._fetchrow_sync(query, args)
        if row is None:
            return None
        return next(iter(row.values()))

    def _execute_sync(self, query: str, args: tuple):
        sql, prepared_args = self._prepare_query(query, args)
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, prepared_args)
            self._conn.commit()
            return "OK"
        except Exception:
            self._conn.rollback()
            raise

    async def fetchrow(self, query: str, *args):
        return await asyncio.to_thread(self._fetchrow_sync, query, args)

    async def fetchval(self, query: str, *args):
        return await asyncio.to_thread(self._fetchval_sync, query, args)

    async def execute(self, query: str, *args):
        return await asyncio.to_thread(self._execute_sync, query, args)


def _strip_psql_meta_commands(sql_text: str) -> str:
    """Remove psql-only commands (e.g. \\connect) before SQL execution via drivers."""
    cleaned_lines = [
        line for line in sql_text.splitlines() if not line.lstrip().startswith("\\")
    ]
    return "\n".join(cleaned_lines)


def _is_test_irrelevant_statement(statement: str) -> bool:
    upper_stmt = statement.upper()
    return (
        upper_stmt.startswith("GRANT ")
        or upper_stmt.startswith("ALTER DEFAULT PRIVILEGES")
        or "SELECT FORMAT(" in upper_stmt
        or "APP_DB_USER" in upper_stmt
    )


def _split_sql_statements(sql_text: str) -> list[str]:
    """Split SQL text into statements while preserving semicolons inside quotes."""
    statements: list[str] = []
    chunk: list[str] = []
    in_single_quote = False
    i = 0

    while i < len(sql_text):
        ch = sql_text[i]

        if ch == "'":
            # Handle escaped single quote in SQL strings: ''
            if in_single_quote and i + 1 < len(sql_text) and sql_text[i + 1] == "'":
                chunk.append(ch)
                chunk.append(sql_text[i + 1])
                i += 2
                continue
            in_single_quote = not in_single_quote
            chunk.append(ch)
            i += 1
            continue

        if ch == ";" and not in_single_quote:
            statement = "".join(chunk).strip()
            if statement:
                statements.append(statement)
            chunk = []
            i += 1
            continue

        chunk.append(ch)
        i += 1

    tail = "".join(chunk).strip()
    if tail:
        statements.append(tail)

    return statements


@pytest.fixture(scope="module")
def db_conn() -> PgConnectionAdapter:
    with PostgresContainer("postgres:16") as postgres:
        dsn = postgres.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        parsed = urlparse(dsn)
        raw_conn = pg8000.connect(
            user=parsed.username,
            password=parsed.password,
            host=parsed.hostname,
            port=parsed.port,
            database=parsed.path.lstrip("/"),
        )
        conn = PgConnectionAdapter(raw_conn)
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        schema_sql = _strip_psql_meta_commands(schema_sql)
        with raw_conn.cursor() as cur:
            for statement in _split_sql_statements(schema_sql):
                if _is_test_irrelevant_statement(statement):
                    continue
                cur.execute(statement)
        raw_conn.commit()
        try:
            yield conn
        finally:
            # Ensure nothing persists between runs in case the container is reused externally.
            asyncio.run(conn.execute("TRUNCATE TABLE user_preferences, users CASCADE"))
            raw_conn.close()


@pytest.fixture(autouse=True)
def cleanup_rows(db_conn: PgConnectionAdapter):
    yield
    asyncio.run(db_conn.execute("TRUNCATE TABLE user_preferences, users CASCADE"))


@asynccontextmanager
async def _real_get_connection(conn: PgConnectionAdapter):
    yield conn


def _build_test_app(conn: PgConnectionAdapter, user_id: UUID) -> FastAPI:
    app = FastAPI()
    app.include_router(preferences_router.router)

    async def fake_current_user():
        return {
            "id": str(user_id),
            "username": "driver",
            "roles": ["user"],
        }

    # Patch module-level dependency used directly by router functions.
    preferences_router.get_connection = lambda: _real_get_connection(conn)
    preferences_router.ensure_user_exists = _ensure_user_exists_for_test
    app.dependency_overrides[preferences_router.get_current_user] = fake_current_user
    return app


def test_repository_creates_defaults_and_updates_fields(db_conn: PgConnectionAdapter):
    conn = db_conn
    repo = PreferenceRepository(conn)
    user_id = uuid4()

    asyncio.run(_ensure_user_exists_for_test(conn, user_id, "driver"))

    created = asyncio.run(repo.get_or_create_defaults(user_id))
    assert created.user_id == user_id
    assert created.appearance.dark_mode is True
    assert created.weather.feels_like is False

    request = UpdatePreferencesRequest.model_validate(
        {
            "appearance": {"dark_mode": False, "colorblind_enabled": True},
            "alerts": {
                "navigation": {"alert": False, "audio": False},
                "traffic": {"alert": False, "audio": True},
            },
            "weather": {
                "wind": True,
                "uv_index": True,
            },
        }
    )

    updated = asyncio.run(repo.update(user_id, request))

    assert updated.appearance.dark_mode is False
    assert updated.appearance.colorblind_enabled is True
    assert updated.alerts.navigation.alert is False
    assert updated.alerts.navigation.audio is False
    assert updated.alerts.traffic.alert is False
    assert updated.alerts.traffic.audio is True
    assert updated.weather.wind is True
    assert updated.weather.uv_index is True


def test_get_preferences_endpoint_creates_user_and_defaults(db_conn: PgConnectionAdapter):
    conn = db_conn
    user_id = uuid4()
    app = _build_test_app(conn, user_id)

    with TestClient(app) as client:
        response = client.get("/api/preferences/")

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == str(user_id)
    assert body["appearance"]["dark_mode"] is True
    created_user = asyncio.run(
        conn.fetchrow("SELECT id FROM users WHERE id = $1", user_id)
    )
    created_prefs = asyncio.run(
        conn.fetchrow("SELECT user_id FROM user_preferences WHERE user_id = $1", user_id)
    )
    assert created_user is not None
    assert created_prefs is not None


def test_patch_preferences_endpoint_updates_state(db_conn: PgConnectionAdapter):
    conn = db_conn
    user_id = uuid4()
    app = _build_test_app(conn, user_id)

    asyncio.run(_ensure_user_exists_for_test(conn, user_id, "driver"))
    asyncio.run(PreferenceRepository(conn).get_or_create_defaults(user_id))

    patch_payload = {
        "appearance": {"dark_mode": False, "colorblind_enabled": True},
        "alerts": {
            "accident": {"alert": True, "audio": False},
            "weather": {"alert": False, "audio": False},
        },
        "weather": {
            "feels_like": True,
            "pressure": True,
        },
    }

    with TestClient(app) as client:
        response = client.patch("/api/preferences/", json=patch_payload)

    assert response.status_code == 200
    body = response.json()
    assert body["appearance"]["dark_mode"] is False
    assert body["appearance"]["colorblind_enabled"] is True
    assert body["alerts"]["accident"]["audio"] is False
    assert body["alerts"]["weather"]["alert"] is False
    assert body["weather"]["feels_like"] is True
    assert body["weather"]["pressure"] is True

    row = asyncio.run(
        conn.fetchrow(
            """
            SELECT dark_mode, colorblind_enabled, alert_accident_audio, alert_weather,
                   weather_feels_like, weather_pressure
            FROM user_preferences
            WHERE user_id = $1
            """,
            user_id,
        )
    )
    assert row is not None
    assert row["dark_mode"] is False
    assert row["colorblind_enabled"] is True
    assert row["alert_accident_audio"] is False
    assert row["alert_weather"] is False
    assert row["weather_feels_like"] is True
    assert row["weather_pressure"] is True
