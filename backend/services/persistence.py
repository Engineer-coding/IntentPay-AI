"""
IntentPay AI - Persistence Facade
=================================
Kalıcılık katmanı için public API.

Varsayılan backend SQLite'tır. Servisler bu facade üzerinden çalışır;
böylece ileride PostgreSQL adapter eklenirken parser / policy / risk /
decision / API katmanları değişmeden kalır.
"""
from __future__ import annotations

import os
from typing import Protocol


class PersistenceAdapter(Protocol):
    def init_db(self, reset: bool = False) -> None: ...
    def save_mandate(self, mandate_id: str, user_id: str, data: dict, status: str, ts: int) -> None: ...
    def save_transaction(self, tx_id: str, user_id: str, data: dict, status: str, ts: int) -> None: ...
    def save_token(self, token_id: str, data: dict, status: str, ts: int) -> None: ...
    def save_audit(self, log_id: str, tx_id: str, event_type: str, details: dict, ts: int) -> None: ...
    def load_all(self) -> dict: ...
    def stats(self) -> dict: ...


_BACKEND = os.environ.get("PERSISTENCE_BACKEND", "sqlite").strip().lower()


def _adapter() -> PersistenceAdapter:
    if _BACKEND == "sqlite":
        from services import persistence_sqlite
        return persistence_sqlite

    raise RuntimeError(
        f"Unsupported persistence backend: {_BACKEND}. "
        "Currently supported: sqlite"
    )


def init_db(reset: bool = False) -> None:
    return _adapter().init_db(reset=reset)


def save_mandate(mandate_id: str, user_id: str, data: dict, status: str, ts: int) -> None:
    return _adapter().save_mandate(mandate_id, user_id, data, status, ts)


def save_transaction(tx_id: str, user_id: str, data: dict, status: str, ts: int) -> None:
    return _adapter().save_transaction(tx_id, user_id, data, status, ts)


def save_token(token_id: str, data: dict, status: str, ts: int) -> None:
    return _adapter().save_token(token_id, data, status, ts)


def save_audit(log_id: str, tx_id: str, event_type: str, details: dict, ts: int) -> None:
    return _adapter().save_audit(log_id, tx_id, event_type, details, ts)


def load_all() -> dict:
    return _adapter().load_all()


def stats() -> dict:
    return _adapter().stats()
