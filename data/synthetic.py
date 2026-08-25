"""
IntentPay AI - Sentetik Veri Üreteci
====================================
Gerçek finansal veri YOKTUR. Demo ve risk modeli eğitimi için deterministik
sentetik veri üretir (sabit seed => tekrarlanabilir demo).
"""
from __future__ import annotations

import random

from models.schema import (
    Agent, Merchant, User, now_ms, _id,
)

SEED = 42


# --------------------------------------------------------------------------- #
#  Demo marketplace - ajan simülatörünün seçtiği ürünler
# --------------------------------------------------------------------------- #
PRODUCTS = [
    # id,            name,                     category,           price,  merchant_key
    ("p_chair",   "Ergonomik Ofis Sandalyesi", "office_furniture",  3700, "m_ofisplus"),
    ("p_chair_p", "Premium Ofis Sandalyesi",   "office_furniture",  5900, "m_ofisplus"),
    ("p_sofa",    "Ofis Bekleme Koltuğu Takımı","office_furniture", 18500, "m_ofisplus"),
    ("p_desk",    "Ayarlanabilir Ofis Masası", "office_furniture",  4500, "m_ofisplus"),
    ("p_paper",   "A4 Fotokopi Kağıdı (5 koli)","office_supplies",    850, "m_kirtasiye"),
    ("p_pens",    "Kalem & Defter Seti",       "office_supplies",    320, "m_kirtasiye"),
    ("p_clean",   "Ofis Temizlik Seti",        "cleaning",           640, "m_temizko"),
    ("p_laptop",  "Dizüstü Bilgisayar",        "electronics",       28900, "m_teknohan"),
    ("p_giftcard","Elektronik Hediye Kartı",   "gift_cards",         2000, "m_yenisatici"),
]


def get_products() -> list[dict]:
    return [
        {"product_id": p[0], "name": p[1], "category": p[2],
         "price": p[3], "merchant_id": p[4]}
        for p in PRODUCTS
    ]


# --------------------------------------------------------------------------- #
#  Fixed demo entities
# --------------------------------------------------------------------------- #
def seed_users() -> dict[str, User]:
    return {
        "u_acme": User(
            user_id="u_acme",
            company_name="Acme Ofis Çözümleri A.Ş.",
            user_type="sme",
            average_transaction_amount=2800.0,
            risk_profile="low",
        )
    }


def seed_agents() -> dict[str, Agent]:
    return {
        "a_buyer": Agent(
            agent_id="a_buyer",
            user_id="u_acme",
            agent_name="ProcureBot v2",
            trust_level="high",
            created_at=now_ms() - 120 * 86_400_000,   # 120 gün önce
            status="active",
        ),
        "a_fresh": Agent(
            agent_id="a_fresh",
            user_id="u_acme",
            agent_name="YeniAsistan",
            trust_level="low",
            created_at=now_ms() - 1 * 86_400_000,      # 1 gün önce (riskli)
            status="active",
        ),
    }


def seed_merchants() -> dict[str, Merchant]:
    data = [
        # id,             name,                  category,           trust, approved, mcc
        ("m_ofisplus",   "OfisPlus Mağazası",   "office_furniture", 0.94, True,     "5712"),
        ("m_kirtasiye",  "Kırtasiye Dünyası",   "office_supplies",  0.91, True,     "5943"),
        ("m_temizko",    "TemizKO Tedarik",     "cleaning",         0.88, True,     "7349"),
        ("m_teknohan",   "TeknoHan Elektronik", "electronics",      0.82, True,     "5732"),
        ("m_yenisatici", "YeniSatıcı Online",   "gift_cards",       0.41, False,    "5816"),  # yeni & riskli
    ]
    return {
        m[0]: Merchant(
            merchant_id=m[0],
            merchant_name=m[1],
            category=m[2],
            trust_score=m[3],
            is_approved=m[4],
            country="TR",
            mcc_code=m[5],
        )
        for m in data
    }


# --------------------------------------------------------------------------- #
#  Training data for the risk model
# --------------------------------------------------------------------------- #
def generate_training_data(n: int = 4000) -> tuple[list[list[float]], list[int]]:
    """
    Risk modeli için sentetik etiketli veri.
    Etiket = 1 (riskli/fraud) eğer işlem birden çok şüpheli sinyal taşıyorsa.
    Özellik sırası feature_vector() ile AYNI olmalı (risk_model.py).
    """
    rng = random.Random(SEED)
    X: list[list[float]] = []
    y: list[int] = []

    for _ in range(n):
        amount_ratio   = rng.choices([0.3, 0.8, 1.0, 1.4, 2.5],
                                     weights=[3, 4, 3, 2, 1])[0]
        merchant_trust = rng.uniform(0.3, 0.98)
        is_new_merch   = rng.choices([0, 1], weights=[7, 3])[0]
        category_mism  = rng.choices([0, 1], weights=[8, 2])[0]
        attempt_count  = rng.choices([0, 1, 2, 3], weights=[6, 2, 1, 1])[0]
        hour           = rng.randint(0, 23)
        odd_hour       = 1 if (hour < 6 or hour > 22) else 0
        agent_age      = rng.choices([1, 10, 60, 120], weights=[2, 2, 3, 3])[0]
        young_agent    = 1 if agent_age < 7 else 0
        prior_fail     = rng.choices([0, 1], weights=[8, 2])[0]
        token_reuse    = rng.choices([0, 1], weights=[9, 1])[0]
        device_change  = rng.choices([0, 1], weights=[8, 2])[0]
        mandate_mism   = rng.choices([0, 1, 2], weights=[7, 2, 1])[0]

        feats = [
            amount_ratio, merchant_trust, is_new_merch, category_mism,
            attempt_count, odd_hour, young_agent, prior_fail,
            token_reuse, device_change, mandate_mism,
        ]

        # --- latent risk: birden çok sinyal birleşince fraud olasılığı artar ---
        risk = (
            0.9 * token_reuse +
            0.7 * category_mism +
            0.5 * mandate_mism +
            0.4 * is_new_merch +
            0.4 * (1 - merchant_trust) +
            0.3 * max(0.0, amount_ratio - 1.0) +
            0.3 * prior_fail +
            0.25 * young_agent +
            0.2 * device_change +
            0.15 * odd_hour +
            0.1 * min(attempt_count, 3)
        )
        prob = 1 / (1 + 2.71828 ** (-(risk - 1.1) * 2.2))
        label = 1 if rng.random() < prob else 0
        X.append(feats)
        y.append(label)

    return X, y


FEATURE_NAMES = [
    "amount_ratio", "merchant_trust", "is_new_merchant", "category_mismatch",
    "attempt_count", "odd_hour", "young_agent", "prior_failure",
    "token_reuse", "device_change", "mandate_mismatch",
]

# İnsan-okunur açıklama etiketleri (explainability için)
FEATURE_LABELS = {
    "amount_ratio":      "İşlem tutarının ortalamaya oranı",
    "merchant_trust":    "Satıcı güven puanı",
    "is_new_merchant":   "Yeni/onaysız satıcı",
    "category_mismatch": "Kategori uyumsuzluğu",
    "attempt_count":     "Tekrarlı deneme sayısı",
    "odd_hour":          "Olağandışı işlem saati",
    "young_agent":       "Ajanın yeni oluşturulmuş olması",
    "prior_failure":     "Önceki başarısız denemeler",
    "token_reuse":       "Ödeme token'ı tekrar kullanımı",
    "device_change":     "Cihaz/IP değişikliği",
    "mandate_mismatch":  "Mandate ile uyumsuzluk sayısı",
}