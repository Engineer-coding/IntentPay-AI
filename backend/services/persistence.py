"""
IntentPay AI - Kalıcılık Katmanı (SQLite)
=========================================
Tüm durum (mandate, işlem, token, audit) diske yazılır; sunucu yeniden
başlasa bile veri korunur. Sıfır bağımlılık: Python'un yerleşik sqlite3'ü.

Tasarım: basit bir key-value + olay tablosu. Karmaşık ORM yok; demo için
şeffaf, denetlenebilir ve hızlı.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "intentpay.db")
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db(reset: bool = False) -> None:
    """Tabloları oluşturur. reset=True ise mevcut veriyi siler (demo başlangıcı)."""
    with _lock, _conn() as c:
        if reset:
            for t in ("mandates", "transactions", "tokens", "audit_events"):
                c.execute(f"DROP TABLE IF EXISTS {t}")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS mandates (
            mandate_id TEXT PRIMARY KEY,
            user_id    TEXT,
            data       TEXT,
            status     TEXT,
            updated_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id TEXT PRIMARY KEY,
            user_id        TEXT,
            data           TEXT,
            status         TEXT,
            created_at     INTEGER
        );
        CREATE TABLE IF NOT EXISTS tokens (
            token_id   TEXT PRIMARY KEY,
            data       TEXT,
            status     TEXT,
            updated_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS audit_events (
            log_id         TEXT PRIMARY KEY,
            transaction_id TEXT,
            event_type     TEXT,
            details        TEXT,
            timestamp      INTEGER
        );
        """)


# --------------------------------------------------------------------------- #
#  Generic upsert / load helpers
# --------------------------------------------------------------------------- #
def save_mandate(mandate_id: str, user_id: str, data: dict, status: str, ts: int) -> None:
    with _lock, _conn() as c:
        c.execute("""INSERT INTO mandates(mandate_id,user_id,data,status,updated_at)
                     VALUES(?,?,?,?,?)
                     ON CONFLICT(mandate_id) DO UPDATE SET
                       data=excluded.data, status=excluded.status,
                       updated_at=excluded.updated_at""",
                  (mandate_id, user_id, json.dumps(data), status, ts))


def save_transaction(tx_id: str, user_id: str, data: dict, status: str, ts: int) -> None:
    with _lock, _conn() as c:
        c.execute("""INSERT INTO transactions(transaction_id,user_id,data,status,created_at)
                     VALUES(?,?,?,?,?)
                     ON CONFLICT(transaction_id) DO UPDATE SET
                       data=excluded.data, status=excluded.status""",
                  (tx_id, user_id, json.dumps(data), status, ts))


def save_token(token_id: str, data: dict, status: str, ts: int) -> None:
    with _lock, _conn() as c:
        c.execute("""INSERT INTO tokens(token_id,data,status,updated_at)
                     VALUES(?,?,?,?)
                     ON CONFLICT(token_id) DO UPDATE SET
                       data=excluded.data, status=excluded.status,
                       updated_at=excluded.updated_at""",
                  (token_id, json.dumps(data), status, ts))


def save_audit(log_id: str, tx_id: str, event_type: str, details: dict, ts: int) -> None:
    with _lock, _conn() as c:
        c.execute("""INSERT OR IGNORE INTO audit_events
                     (log_id,transaction_id,event_type,details,timestamp)
                     VALUES(?,?,?,?,?)""",
                  (log_id, tx_id, event_type, json.dumps(details), ts))


def load_all() -> dict:
    """Sunucu açılışında tüm kalıcı durumu geri yükler."""
    with _lock, _conn() as c:
        mandates = [dict(r) for r in c.execute("SELECT * FROM mandates")]
        txns     = [dict(r) for r in c.execute("SELECT * FROM transactions")]
        tokens   = [dict(r) for r in c.execute("SELECT * FROM tokens")]
        audit    = [dict(r) for r in c.execute(
                    "SELECT * FROM audit_events ORDER BY timestamp ASC")]
    return {
        "mandates": [{**m, "data": json.loads(m["data"])} for m in mandates],
        "transactions": [{**t, "data": json.loads(t["data"])} for t in txns],
        "tokens": [{**t, "data": json.loads(t["data"])} for t in tokens],
        "audit": [{**a, "details": json.loads(a["details"])} for a in audit],
    }


def stats() -> dict:
    with _lock, _conn() as c:
        def n(t): return c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return {
            "mandates": n("mandates"),
            "transactions": n("transactions"),
            "tokens": n("tokens"),
            "audit_events": n("audit_events"),
            "db_path": os.path.abspath(_DB_PATH),
        }