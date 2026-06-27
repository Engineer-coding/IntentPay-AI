"""
IntentPay AI - Audit Log Module
===============================
Tüm işlem yaşam döngüsünü denetlenebilir şekilde kaydeder. Fraud analizi,
chargeback incelemesi veya kullanıcı itirazı için kanıt zinciri oluşturur.

Her işlem için sıralı olaylar tutulur: intent -> mandate -> approval ->
transaction -> policy -> risk -> decision -> token.
"""
from __future__ import annotations

from dataclasses import asdict

from models.schema import AuditLog, _id, now_ms


class AuditTrail:
    def __init__(self) -> None:
        self._logs: list[AuditLog] = []

    def record(self, transaction_id: str, event_type: str, details: dict) -> AuditLog:
        log = AuditLog(
            log_id=_id("log"),
            transaction_id=transaction_id,
            event_type=event_type,
            details=details,
            timestamp=now_ms(),
        )
        self._logs.append(log)
        return log

    def for_transaction(self, transaction_id: str) -> list[dict]:
        return [asdict(l) for l in self._logs if l.transaction_id == transaction_id]

    def all(self, limit: int = 200) -> list[dict]:
        return [asdict(l) for l in self._logs[-limit:][::-1]]

    def grouped_by_transaction(self) -> list[dict]:
        """İşlem bazında gruplanmış, özetlenmiş kayıtlar (audit ekranı için)."""
        groups: dict[str, list[AuditLog]] = {}
        for l in self._logs:
            groups.setdefault(l.transaction_id, []).append(l)

        out = []
        for tx_id, logs in groups.items():
            logs_sorted = sorted(logs, key=lambda x: x.timestamp)
            decision_log = next(
                (l for l in reversed(logs_sorted) if l.event_type == "decision"), None)
            summary = decision_log.details if decision_log else {}
            out.append({
                "transaction_id": tx_id,
                "events": [asdict(l) for l in logs_sorted],
                "final_decision": summary.get("final_decision"),
                "explanation": summary.get("explanation"),
                "timestamp": logs_sorted[0].timestamp,
            })
        return sorted(out, key=lambda x: x["timestamp"], reverse=True)