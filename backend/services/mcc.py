"""
IntentPay AI - MCC Domain Helpers
=================================
Kartlı ödeme dünyasında merchant kategorisi çoğunlukla MCC
(Merchant Category Code) üzerinden gelir.

Bu modül demo amaçlı MCC -> kategori eşlemesini merkezi olarak tutar.
Policy engine, risk açıklamaları ve audit log aynı kaynağı kullanabilir.
"""
from __future__ import annotations

from models.schema import CATEGORIES


MCC_CATEGORY_MAP: dict[str, str] = {
    # User-specified fintech demo mappings
    "5943": "office_supplies",
    "5732": "electronics",
    "5816": "gift_cards",
    "5999": "miscellaneous",

    # Existing demo marketplace compatibility
    # Office chair/furniture ürünlerinin güvenli senaryoda yanlışlıkla
    # mismatch üretmemesi için ayrı tutulur.
    "5712": "office_furniture",
    "7349": "cleaning",
}


MCC_LABELS: dict[str, str] = {
    "5943": "Office Supplies",
    "5732": "Electronics",
    "5816": "Digital Goods",
    "5999": "Miscellaneous",
    "5712": "Office Furniture",
    "7349": "Cleaning Services",
}


def normalize_mcc(mcc_code: str | int | None) -> str:
    """MCC değerini güvenli string formuna çevirir."""
    if mcc_code is None:
        return ""
    return str(mcc_code).strip()


def category_for_mcc(mcc_code: str | int | None) -> str | None:
    """MCC kodundan sistem içi kategori key'i döndürür."""
    return MCC_CATEGORY_MAP.get(normalize_mcc(mcc_code))


def label_for_mcc(mcc_code: str | int | None) -> str:
    """MCC kodu için insan okunur etiket döndürür."""
    code = normalize_mcc(mcc_code)
    return MCC_LABELS.get(code, "Unknown MCC")


def category_label_for_mcc(mcc_code: str | int | None) -> str:
    """MCC kodunun işaret ettiği kategorinin Türkçe etiketini döndürür."""
    category = category_for_mcc(mcc_code)
    if not category:
        return "Bilinmeyen kategori"
    return CATEGORIES.get(category, category)


def explain_mcc(mcc_code: str | int | None) -> dict:
    """Audit ve debug için MCC açıklama objesi üretir."""
    code = normalize_mcc(mcc_code)
    category = category_for_mcc(code)
    return {
        "mcc_code": code,
        "mcc_label": label_for_mcc(code),
        "category": category,
        "category_label": category_label_for_mcc(code),
        "known": category is not None,
    }
