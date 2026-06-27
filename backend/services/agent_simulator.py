"""
IntentPay AI - Agent Simulator
==============================
AI ajanı gibi davranarak ödeme istekleri üretir. Jüriye farklı risk senaryoları
göstermek için bilinçli olarak hem güvenli hem riskli işlemler üretebilir.

Senaryolar:
  - safe          : limit içi, izinli kategori, güvenilir satıcı  -> approve
  - over_limit    : limiti aşan tutar                              -> step-up / decline
  - category_block: yasaklı/izinsiz kategori                       -> decline
  - new_merchant  : yeni/onaysız satıcı                            -> step-up / review
  - token_reuse   : kullanılmış token tekrar denenir              -> decline
"""
from __future__ import annotations

from models.schema import TransactionRequest, now_ms, _id
from data.synthetic import get_products

_PRODUCTS = {p["product_id"]: p for p in get_products()}


SCENARIOS = {
    "safe": {
        "label": "Güvenli İşlem",
        "product_id": "p_chair",          # 3700 TL ofis sandalyesi
        "agent_id": "a_buyer",
        "description": "Limit içinde, izinli kategori, güvenilir satıcı.",
    },
    "over_limit": {
        "label": "Limit Aşımı",
        "product_id": "p_chair_x",        # 6200 TL koltuk
        "agent_id": "a_buyer",
        "description": "İşlem tutarı kullanıcının belirlediği limiti aşıyor.",
    },
    "category_block": {
        "label": "Kategori İhlali",
        "product_id": "p_giftcard",       # elektronik hediye kartı
        "agent_id": "a_buyer",
        "description": "Seçilen kategori mandate tarafından yasaklanmış.",
    },
    "new_merchant": {
        "label": "Yeni / Riskli Satıcı",
        "product_id": "p_laptop",         # onaylı ama yüksek tutar + yeni ajan
        "agent_id": "a_fresh",
        "description": "Yeni oluşturulmuş ajan, yüksek tutarlı işlem deniyor.",
    },
    "token_reuse": {
        "label": "Token Tekrar Kullanımı",
        "product_id": "p_chair",
        "agent_id": "a_buyer",
        "description": "Daha önce kullanılmış ödeme token'ı tekrar deneniyor (replay).",
    },
}


def build_request(scenario: str, user_id: str, reused_token_id: str | None = None) -> dict:
    spec = SCENARIOS.get(scenario, SCENARIOS["safe"])
    product = _PRODUCTS[spec["product_id"]]

    tx = TransactionRequest(
        transaction_id=_id("tx"),
        user_id=user_id,
        agent_id=spec["agent_id"],
        merchant_id=product["merchant_id"],
        amount=float(product["price"]),
        currency="TRY",
        category=product["category"],
        timestamp=now_ms(),
        cart_description=product["name"],
        note=f"[{spec['label']}] {spec['description']}",
        reused_token_id=reused_token_id if scenario == "token_reuse" else None,
        status="pending",
    )
    return {
        "transaction": tx.__dict__,
        "scenario": scenario,
        "scenario_label": spec["label"],
        "product": product,
    }


def list_scenarios() -> list[dict]:
    return [
        {"key": k, "label": v["label"], "description": v["description"],
         "product": _PRODUCTS[v["product_id"]]["name"],
         "amount": _PRODUCTS[v["product_id"]]["price"]}
        for k, v in SCENARIOS.items()
    ]