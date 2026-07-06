"""
IntentPay AI - Backend API
==========================
Sıfır-bağımlılık HTTP sunucusu (Python stdlib). Tüm servisleri orkestre eder.

Endpointler:
  GET  /api/bootstrap            -> kullanıcılar, satıcılar, ajanlar, ürünler, senaryolar
  POST /api/intent/parse         -> doğal dil -> mandate (onay bekler)
  POST /api/mandate/approve      -> mandate'i aktifleştir
  POST /api/agent/request        -> ajan simülatörü ödeme isteği üretir
  POST /api/transaction/evaluate -> policy + risk + karar + token + audit
  GET  /api/audit                -> denetim kayıtları (işlem bazında gruplu)
  GET  /api/tokens               -> üretilmiş tokenlar
  GET  /api/health

Tüm durum bellekte (demo). Gerçek ödeme / kart / banka YOK.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))

from models.schema import (
    Mandate, TransactionRequest, now_ms,
)
from data.synthetic import (
    seed_users, seed_agents, seed_merchants, get_products,
)
from services.intent_parser import parse_intent
from services.policy_engine import evaluate_policy
from services.risk_model import feature_vector, score_transaction
from services.decision_engine import decide
from services.token_sim import TokenStore
from services.audit_log import AuditTrail
from services.agent_simulator import build_request, list_scenarios
from services.attack_detector import scan_text, sanitize_mandate
from services import persistence


# --------------------------------------------------------------------------- #
#  Shared application state
# --------------------------------------------------------------------------- #
from services.app_state import AppState, STATE, DEFAULT_USER
from services import api_core
from services.transaction_service import evaluate_transaction as evaluate_transaction_service


# --------------------------------------------------------------------------- #
#  Core orchestration: evaluate a transaction end-to-end
# --------------------------------------------------------------------------- #
def evaluate_transaction(tx_dict: dict) -> dict:
    """Backward-compatible wrapper for legacy tests and stdlib server."""
    return evaluate_transaction_service(tx_dict)


# --------------------------------------------------------------------------- #
#  HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):           # sessiz log
        pass

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/health":
            self._send(200, {"status": "ok", "time": now_ms()})
        elif path == "/api/bootstrap":
            self._send(200, self._bootstrap())
        elif path == "/api/audit":
            self._send(200, {"transactions": STATE.audit.grouped_by_transaction(),
                             "raw": STATE.audit.all()})
        elif path == "/api/tokens":
            self._send(200, {"tokens": STATE.tokens.all()})
        elif path == "/api/analytics":
            self._send(200, self._analytics())
        elif path == "/api/persistence":
            self._send(200, persistence.stats())
        elif path.startswith("/api/"):
            self._send(404, {"error": "not found"})
        else:
            self._serve_static(path)

    def _serve_static(self, path):
        """Frontend dosyalarını servis eder (demo: tek komutla çalışsın)."""
        fe_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.normpath(os.path.join(fe_dir, rel))
        if not full.startswith(os.path.normpath(fe_dir)) or not os.path.isfile(full):
            self.send_response(404); self.end_headers(); return
        ctype = ("text/html" if full.endswith(".html")
                 else "text/javascript" if full.endswith(".jsx") or full.endswith(".js")
                 else "text/css" if full.endswith(".css")
                 else "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            body = self._body()
            if path == "/api/intent/parse":
                self._send(200, self._parse(body))
            elif path == "/api/mandate/approve":
                self._send(200, self._approve(body))
            elif path == "/api/agent/request":
                self._send(200, self._agent_request(body))
            elif path == "/api/transaction/evaluate":
                self._send(200, evaluate_transaction(body["transaction"]))
            elif path == "/api/security/scan":
                self._send(200, scan_text(body.get("text", "")))
            elif path == "/api/stepup/resolve":
                self._send(200, self._resolve_stepup(body))
            elif path == "/api/token/tamper":
                self._send(200, self._tamper_token(body))
            elif path == "/api/risk/threshold":
                self._send(200, self._set_threshold(body))
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(500, {"error": str(e)})

    # --- handlers ---
    def _bootstrap(self) -> dict:
        return api_core.bootstrap()

    def _parse(self, body: dict) -> dict:
        return api_core.parse_intent(body)

    def _approve(self, body: dict) -> dict:
        return api_core.approve_mandate(body)

    def _agent_request(self, body: dict) -> dict:
        return api_core.agent_request(body)

    def _tamper_token(self, body: dict) -> dict:
        return api_core.tamper_token(body)

    def _set_threshold(self, body: dict) -> dict:
        return api_core.set_risk_threshold(body)

    def _resolve_stepup(self, body: dict) -> dict:
        return api_core.resolve_stepup(body)

    def _analytics(self) -> dict:
        return api_core.analytics()


def main():
    port = int(os.environ.get("PORT", 8787))
    # Kalıcılık: DB'yi hazırla. RESET_DB=1 ile temiz başla (demo).
    reset = os.environ.get("RESET_DB", "0") == "1"
    persistence.init_db(reset=reset)
    if not reset:
        STATE.restore()
        st = persistence.stats()
        print(f"Kalıcı durum yüklendi: {st['mandates']} mandate, "
              f"{st['transactions']} işlem, {st['tokens']} token, "
              f"{st['audit_events']} audit olayı.")
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"IntentPay AI backend çalışıyor:  http://localhost:{port}")
    print(f"Veritabanı: {persistence.stats()['db_path']}")
    server.serve_forever()


if __name__ == "__main__":
    main()