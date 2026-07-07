"""
IntentPay AI - Standard API Response Envelope
=============================================
FastAPI HTTP katmanı için tek tip response formatı.

Başarılı cevap:
{
    "ok": true,
    "data": {...}
}

Hata cevabı:
{
    "ok": false,
    "error": {
        "code": "...",
        "message": "...",
        "details": {...}
    }
}
"""
from __future__ import annotations

from typing import Any


def success(data: Any) -> dict[str, Any]:
    """Başarılı API cevabını standart envelope içine alır."""
    return {
        "ok": True,
        "data": data,
    }


def failure(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Hata API cevabını standart envelope içine alır."""
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }


def infer_error_code(message: str, default_code: str = "BAD_REQUEST") -> str:
    """Eski string tabanlı hata mesajlarını daha net hata kodlarına çevirir."""
    normalized = (message or "").lower()

    if "bilinmeyen senaryo" in normalized:
        return "UNKNOWN_SCENARIO"
    if "boş" in normalized or "eksik" in normalized:
        return "INVALID_INPUT"
    if "mandate" in normalized and "bulunamad" in normalized:
        return "MANDATE_NOT_FOUND"
    if "transaction" in normalized and "bulunamad" in normalized:
        return "TRANSACTION_NOT_FOUND"
    if "token" in normalized and "bulunamad" in normalized:
        return "TOKEN_NOT_FOUND"

    return default_code


def envelope(
    payload: Any,
    default_error_code: str = "BAD_REQUEST",
) -> dict[str, Any]:
    """
    api_core'dan gelen eski response'ları standart HTTP envelope'a çevirir.

    api_core şu an backward-compatible olarak raw dict döndürür. Bu fonksiyon
    HTTP contract'ını standardize ederken core servis davranışını bozmaz.
    """
    if isinstance(payload, dict) and "error" in payload:
        raw_error = payload.get("error")
        message = str(raw_error or "İstek işlenemedi.")
        details = {k: v for k, v in payload.items() if k != "error"}
        return failure(
            infer_error_code(message, default_error_code),
            message,
            details,
        )

    return success(payload)


def exception_response(
    exc: Exception,
    code: str = "INTERNAL_ERROR",
) -> dict[str, Any]:
    """Beklenmeyen exception'ları standart hata cevabına çevirir."""
    return failure(code, str(exc), {})
