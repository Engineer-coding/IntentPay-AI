"""
IntentPay AI - PostgreSQL Persistence Adapter
============================================
Production-oriented PostgreSQL persistence adapter.

Özellikler:
- SQLite adapter ile aynı public API
- schema_migrations tablosu
- connection pooling
- commit / rollback kontrollü transaction akışı
"""
from __future__ import annotations

import atexit
import json
import os
import threading
from contextlib import contextmanager
from urllib.parse import urlparse

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
except ImportError as exc:
    raise RuntimeError(
        "PostgreSQL backend requires psycopg and psycopg_pool. "
        "Install with: python -m pip install 'psycopg[binary]' psycopg_pool"
    ) from exc

SCHEMA_VERSION = "001_initial_schema"

_lock = threading.Lock()
_pool_lock = threading.Lock()
_pool: ConnectionPool | None = None


def _require_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is required when PERSISTENCE_BACKEND=postgres. "
            "Example: postgresql://intentpay:intentpay@localhost:5432/intentpay"
        )
    return url


def _pool_min_size() -> int:
    return int(os.environ.get("POSTGRES_POOL_MIN_SIZE", "1"))


def _pool_max_size() -> int:
    return int(os.environ.get("POSTGRES_POOL_MAX_SIZE", "5"))


def _connect_timeout() -> int:
    return int(os.environ.get("POSTGRES_CONNECT_TIMEOUT", "5"))


def _safe_database_label() -> str:
    parsed = urlparse(_require_database_url())
    host = parsed.hostname or "unknown-host"
    port = f":{parsed.port}" if parsed.port else ""
    db = parsed.path.lstrip("/") or "unknown-db"
    return f"postgresql://{host}{port}/{db}"



def close_pool() -> None:
    """Process kapanırken PostgreSQL connection pool'u temiz kapatır."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close(timeout=5.0)
            _pool = None


atexit.register(close_pool)


def _get_pool() -> ConnectionPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(
                conninfo=_require_database_url(),
                min_size=_pool_min_size(),
                max_size=_pool_max_size(),
                timeout=_connect_timeout(),
                kwargs={"row_factory": dict_row},
                open=True,
            )
        return _pool


@contextmanager
def _conn():
    pool = _get_pool()
    with pool.connection() as c:
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def _apply_migrations(c) -> None:
    c.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            applied_at BIGINT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS mandates (
            mandate_id TEXT PRIMARY KEY,
            user_id    TEXT,
            data       TEXT,
            status     TEXT,
            updated_at BIGINT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id TEXT PRIMARY KEY,
            user_id        TEXT,
            data           TEXT,
            status         TEXT,
            created_at     BIGINT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            token_id   TEXT PRIMARY KEY,
            data       TEXT,
            status     TEXT,
            updated_at BIGINT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_events (
            log_id         TEXT PRIMARY KEY,
            transaction_id TEXT,
            event_type     TEXT,
            details        TEXT,
            timestamp      BIGINT
        )
    """)

    c.execute(
        """
        INSERT INTO schema_migrations(version, applied_at)
        VALUES(%s, CAST(EXTRACT(EPOCH FROM NOW()) * 1000 AS BIGINT))
        ON CONFLICT(version) DO NOTHING
        """,
        (SCHEMA_VERSION,),
    )


def init_db(reset: bool = False) -> None:
    with _lock, _conn() as c:
        if reset:
            for table in ("audit_events", "tokens", "transactions", "mandates", "schema_migrations"):
                c.execute(f"DROP TABLE IF EXISTS {table}")
        _apply_migrations(c)


def save_mandate(
    mandate_id: str,
    user_id: str,
    data: dict,
    status: str,
    ts: int,
) -> None:
    with _lock, _conn() as c:
        c.execute(
            """
            INSERT INTO mandates(mandate_id, user_id, data, status, updated_at)
            VALUES(%s, %s, %s, %s, %s)
            ON CONFLICT(mandate_id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                data = EXCLUDED.data,
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
            """,
            (mandate_id, user_id, json.dumps(data), status, ts),
        )


def save_transaction(
    tx_id: str,
    user_id: str,
    data: dict,
    status: str,
    ts: int,
) -> None:
    with _lock, _conn() as c:
        c.execute(
            """
            INSERT INTO transactions(transaction_id, user_id, data, status, created_at)
            VALUES(%s, %s, %s, %s, %s)
            ON CONFLICT(transaction_id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                data = EXCLUDED.data,
                status = EXCLUDED.status
            """,
            (tx_id, user_id, json.dumps(data), status, ts),
        )


def save_token(token_id: str, data: dict, status: str, ts: int) -> None:
    with _lock, _conn() as c:
        c.execute(
            """
            INSERT INTO tokens(token_id, data, status, updated_at)
            VALUES(%s, %s, %s, %s)
            ON CONFLICT(token_id) DO UPDATE SET
                data = EXCLUDED.data,
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
            """,
            (token_id, json.dumps(data), status, ts),
        )


def save_audit(
    log_id: str,
    tx_id: str,
    event_type: str,
    details: dict,
    ts: int,
) -> None:
    with _lock, _conn() as c:
        c.execute(
            """
            INSERT INTO audit_events(log_id, transaction_id, event_type, details, timestamp)
            VALUES(%s, %s, %s, %s, %s)
            ON CONFLICT(log_id) DO NOTHING
            """,
            (log_id, tx_id, event_type, json.dumps(details), ts),
        )


def load_all() -> dict:
    with _lock, _conn() as c:
        mandates = list(c.execute("SELECT * FROM mandates"))
        txns = list(c.execute("SELECT * FROM transactions"))
        tokens = list(c.execute("SELECT * FROM tokens"))
        audit = list(c.execute("SELECT * FROM audit_events ORDER BY timestamp ASC"))

    return {
        "mandates": [{**m, "data": json.loads(m["data"])} for m in mandates],
        "transactions": [{**t, "data": json.loads(t["data"])} for t in txns],
        "tokens": [{**t, "data": json.loads(t["data"])} for t in tokens],
        "audit": [{**a, "details": json.loads(a["details"])} for a in audit],
    }


def _schema_version(c) -> str:
    try:
        row = c.execute(
            "SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()
        return row["version"] if row else "unknown"
    except Exception:
        return "unknown"


def stats() -> dict:
    with _lock, _conn() as c:
        def n(table: str) -> int:
            return c.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]

        return {
            "backend": "postgres",
            "status": "ok",
            "schema_version": _schema_version(c),
            "mandates": n("mandates"),
            "transactions": n("transactions"),
            "tokens": n("tokens"),
            "audit_events": n("audit_events"),
            "db_path": _safe_database_label(),
            "pool": {
                "enabled": True,
                "min_size": _pool_min_size(),
                "max_size": _pool_max_size(),
                "timeout": _connect_timeout(),
            },
        }
