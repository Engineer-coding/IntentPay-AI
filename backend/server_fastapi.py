

"""
IntentPay AI - FastAPI Backend
==============================
Compatibility-first FastAPI migration.

This module keeps the existing endpoint paths and delegates core business
behavior to the current stdlib `server.py` implementation. The goal is to
move the HTTP layer to FastAPI without changing the LLM parser, policy engine,
risk model, decision flow, token logic, persistence, analytics, or audit logic.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import server as core


app = FastAPI(
    title="IntentPay AI API",
    version="0.2.0",
    description="AI agent payment authorization layer demo API.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def handler() -> core.Handler:
    """Create an uninitialized Handler instance to reuse server.py methods."""
    return core.Handler.__new__(core.Handler)


@app.on_event("startup")
def startup() -> None:
    reset = os.environ.get("RESET_DB", "0") == "1"
    core.persistence.init_db(reset=reset)

    if not reset:
        core.STATE.restore()
        st = core.persistence.stats()
        print(
            f"Kalıcı durum yüklendi: {st['mandates']} mandate, "
            f"{st['transactions']} işlem, {st['tokens']} token, "
            f"{st['audit_events']} audit olayı."
        )

    print("IntentPay AI FastAPI backend çalışıyor.")
    print(f"Veritabanı: {core.persistence.stats()['db_path']}")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "time": core.now_ms(),
        "server": "fastapi",
    }


@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    return handler()._bootstrap()


@app.get("/api/audit")
def audit() -> dict[str, Any]:
    return {
        "transactions": core.STATE.audit.grouped_by_transaction(),
        "raw": core.STATE.audit.all(),
    }


@app.get("/api/tokens")
def tokens() -> dict[str, Any]:
    return {"tokens": core.STATE.tokens.all()}


@app.get("/api/analytics")
def analytics() -> dict[str, Any]:
    return handler()._analytics()


@app.get("/api/persistence")
def persistence_stats() -> dict[str, Any]:
    return core.persistence.stats()


@app.post("/api/intent/parse")
def parse_intent(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        return handler()._parse(body)
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/api/mandate/approve")
def approve_mandate(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        return handler()._approve(body)
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/api/agent/request")
def agent_request(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        return handler()._agent_request(body)
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/api/transaction/evaluate")
def transaction_evaluate(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        return core.evaluate_transaction(body["transaction"])
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/api/security/scan")
def security_scan(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        return core.scan_text(body.get("text", ""))
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/api/stepup/resolve")
def stepup_resolve(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        return handler()._resolve_stepup(body)
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/api/token/tamper")
def token_tamper(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        return handler()._tamper_token(body)
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/api/risk/threshold")
def risk_threshold(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        return handler()._set_threshold(body)
    except Exception as exc:
        return {"error": str(exc)}


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    rel_path = "index.html" if path in ("", "/") else path
    full_path = (FRONTEND_DIR / rel_path).resolve()

    try:
        full_path.relative_to(FRONTEND_DIR.resolve())
    except ValueError:
        return JSONResponse({"error": "not found"}, status_code=404)

    if not full_path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)

    return FileResponse(full_path)