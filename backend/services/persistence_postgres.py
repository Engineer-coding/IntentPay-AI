"""
IntentPay AI - PostgreSQL Persistence Adapter
============================================
PostgreSQL kalıcılık adapter'ı.

SQLite adapter ile aynı public fonksiyonları sağlar:
- init_db
- save_mandate
- save_transaction
- save_token
- save_audit
- load_all
- stats

Veri modeli bilinçli olarak mevcut SQLite demo yapısıyla uyumlu tutulur:
key-value JSON payload + audit event table.
"""
from __future__ import annotations

import json
import os
import threading
from urllib.parse import urlparse

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError as exc:
    raise RuntimeError(
        "PostgreSQL backend requires psycopg. "
        "Install it with: python -m pip install 'psycopg[binary]'"
    ) from exc


_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
_lock = threading.Lock()


def _require_database_url() -> str:
    if not _DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is required when PERSISTENCE_BACKEND=postgres. "
            "Example: postgresql://intentpay:intentpay@localhost:5432/intentpay"
        )
    return _DATABASE_URL


def _safe_database_label() -> str:
    url = _require_database_url()
    parsed = urlparse(url)
    host = parsed.hostname or "unknown-host"
    port = f":{parsed.port}" if parsed.port else ""
    db = parsed.path.lstrip("/") or "unknown-db"
    return f"postgresql://{host}{port}/{db}"


def _conn():
    c = psycopg.connect(_require_database_url(), row_factory=dict_row)
    return c


def init_db(reset: bool = False) -> None:
    """Tabloları oluşturur. reset=True ise mevcut veriyi siler."""
    with _lock, _conn() as c:
        if reset:
            for table in ("audit_events", "tokens", "transactions", "mandates"):
                c.execute(f"DROP TABLE IF EXISTS {table}")

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

        c.commit()


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
        c.commit()


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
        c.commit()


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
        c.commit()


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
        c.commit()


def load_all() -> dict:
    """Sunucu açılışında tüm kalıcı durumu geri yükler."""
    with _lock, _conn() as c:
        mandates = list(c.execute("SELECT * FROM mandates"))
        txns = list(c.execute("SELECT * FROM transactions"))
        tokens = list(c.execute("SELECT * FROM tokens"))
        audit = list(c.execute(
            "SELECT * FROM audit_events ORDER BY timestamp ASC"
        ))

    return {
        "mandates": [
            {**m, "data": json.loads(m["data"])}
            for m in mandates
        ],
        "transactions": [
            {**t, "data": json.loads(t["data"])}
            for t in txns
        ],
        "tokens": [
            {**t, "data": json.loads(t["data"])}
            for t in tokens
        ],
        "audit": [
            {**a, "details": json.loads(a["details"])}
            for a in audit
        ],
    }


def stats() -> dict:
    with _lock, _conn() as c:
        def n(table: str) -> int:
            return c.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]

        return {
            "mandates": n("mandates"),
            "transactions": n("transactions"),
            "tokens": n("tokens"),
            "audit_events": n("audit_events"),
            "db_path": _safe_database_label(),
            "backend": "postgres",
        }
