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
        "product_id": "p_chair",          # 3.700 TL sandalye: mobilya, limit içi, onaylı satıcı
        "agent_id": "a_buyer",
        "description": "Ofis sandalyesi · limit içinde, izinli kategori, onaylı satıcı.",
    },
    "over_limit": {
        "label": "Limit Aşımı",
        "product_id": "p_sofa",           # 18.500 TL: mobilya (izinli) ama tutar limitini aşar
        "agent_id": "a_buyer",
        "description": "Koltuk takımı · kategori izinli ama tutar 5.000 TL limitini büyük oranda aşıyor.",
    },
    "category_block": {
        "label": "Kategori İhlali",
        "product_id": "p_laptop",         # elektronik = talimatta açıkça yasaklı
        "agent_id": "a_buyer",
        "description": "Dizüstü bilgisayar · 'elektronik alma' kuralıyla yasaklanan kategori.",
    },
    "stepup_approval": {
        "label": "Ek Onay Gerekli",
        "product_id": "p_chair_p",        # 5.900 TL: mobilya, limiti %18 aşar (≤%25 -> step-up)
        "agent_id": "a_buyer",
        "description": "Premium sandalye · izinli kategori, tutar limiti az miktarda aşıyor.",
    },
    "new_merchant": {
        "label": "Yeni / Riskli Satıcı",
        "product_id": "p_giftcard",       # onaysız satıcı (m_yenisatici) + yeni ajan
        "agent_id": "a_fresh",
        "description": "Onaysız satıcıdan alışveriş · 'yalnızca onaylı satıcı' kuralı ihlali.",
    },
    "token_reuse": {
        "label": "Token Tekrar Kullanımı",
        "product_id": "p_chair",          # ilk onayla üretilen token replay edilir
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