"""
IntentPay AI — RAG Test Paketi
===============================

Bu test paketi, RAG modülünün doğru ve DETERMİNİSTİK çalıştığını doğrular.

KRİTİK TASARIM: Tüm testler mock embedding kullanır (force_mock=True).
Yani internet, OpenAI API anahtarı GEREKMEZ ve her çalıştırmada AYNI sonucu
verir. Bu, daha önce LLM entegrasyonunun testleri kırdığı hatayı önler.

Bu dosya mevcut run_tests.py'ye entegre edilebilir veya ayrı çalıştırılabilir:
    python test_rag.py

Test kapsamı:
  1. Doküman deposu — profiller, kimlik determinizmi, serileştirme
  2. Embedding — determinizm, mock/gerçek ayrımı, vektör deposu
  3. Retriever — arama, şirket izolasyonu, bağlam metni
  4. Entegrasyon — parse_intent geriye uyumluluğu, RAG opsiyonelliği
"""
from __future__ import annotations
import sys
import os
import types


# ---------------------------------------------------------------------------
# Test altyapısı (basit, bağımlılıksız — projenin run_tests.py stiline yakın)
# ---------------------------------------------------------------------------
_PASS = 0
_FAIL = 0
_FAILURES = []


def check(name: str, condition: bool, detail: str = ""):
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  \u2713 PASS  {name}")
    else:
        _FAIL += 1
        _FAILURES.append(name)
        print(f"  \u2717 FAIL  {name}" + (f"  ({detail})" if detail else ""))


# ---------------------------------------------------------------------------
# models.schema mock'u (gerçek proje bağımlılığı olmadan test için)
# Gerçek projede bu mock GEREKMEZ — schema zaten mevcut. Burada testi
# bağımsız çalıştırabilmek için ekli.
# ---------------------------------------------------------------------------
def _install_schema_mock():
    if "models.schema" in sys.modules:
        return
    schema = types.ModuleType("models.schema")

    class Mandate:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def to_dict(self):
            return dict(self.__dict__)

    schema.Mandate = Mandate
    schema.CATEGORIES = {
        "office_furniture": 1, "office_supplies": 1, "cleaning": 1,
        "electronics": 1, "gift_cards": 1,
    }
    schema._id = lambda p: f"{p}_test"
    schema.now_ms = lambda: 1000000
    models_pkg = types.ModuleType("models")
    models_pkg.schema = schema
    sys.modules["models"] = models_pkg
    sys.modules["models.schema"] = schema


# ---------------------------------------------------------------------------
# 1. Doküman deposu testleri
# ---------------------------------------------------------------------------
def test_documents():
    print("\n[1] Doküman Deposu")
    from rag import documents

    companies = documents.list_companies()
    check("En az 2 şirket profili var", len(companies) >= 2,
          f"{len(companies)} profil")

    # Regülasyon profili list_companies'te GÖRÜNMEMELİ (ortak, seçilemez)
    ids = [c["company_id"] for c in companies]
    check("Regülasyon profili şirket listesinde değil",
          "_regulation_tr" not in ids)

    # Bir şirketin dokümanları + regülasyon geliyor mu
    docs = documents.get_documents_for_company("tekno_a")
    has_company = any(d.company_id == "tekno_a" for d in docs)
    has_reg = any(d.company_id == "_regulation_tr" for d in docs)
    check("Şirket dokümanları getiriliyor", has_company)
    check("Regülasyon dokümanları otomatik ekleniyor", has_reg)

    # Kimlik determinizmi
    all_docs = documents.get_all_documents()
    all_ids = [d.doc_id for d in all_docs]
    check("Tüm doküman kimlikleri benzersiz",
          len(all_ids) == len(set(all_ids)))

    # Serileştirme yuvarlak turu
    d0 = all_docs[0]
    d0r = documents.PolicyDocument.from_dict(d0.to_dict())
    check("Serileştirme tutarlı (to_dict/from_dict)", d0r.doc_id == d0.doc_id)

    # Kimlikten doküman bulma
    found = documents.get_document_by_id(d0.doc_id)
    check("Kimlikten doküman bulunuyor", found is not None and found.doc_id == d0.doc_id)


# ---------------------------------------------------------------------------
# 2. Embedding testleri
# ---------------------------------------------------------------------------
def test_embeddings():
    print("\n[2] Embedding & Vektör Deposu")
    from rag import embeddings

    # Determinizm: aynı metin → aynı vektör (mock)
    v1, m1 = embeddings.embed_text("ofis sandalyesi", force_mock=True)
    v2, m2 = embeddings.embed_text("ofis sandalyesi", force_mock=True)
    check("Mock embedding deterministik", v1 == v2)
    check("Mock modu doğru raporlanıyor", m1 == "mock")

    # Farklı metinler farklı vektör
    v3, _ = embeddings.embed_text("tamamen farklı bir cümle", force_mock=True)
    check("Farklı metinler farklı vektör üretir", v1 != v3)

    # Vektör deposu inşası
    store = embeddings.VectorStore(index_path=None)
    stats = store.build(embeddings.get_all_documents(), force_mock=True)
    check("İndeks tüm dokümanları içeriyor",
          stats["total"] == len(embeddings.get_all_documents()))
    check("İlk build'de hepsi yeni embed edildi", stats["new"] == stats["total"])

    # Önbellek: ikinci build yeni embed yapmamalı
    stats2 = store.build(embeddings.get_all_documents(), force_mock=True)
    check("İkinci build önbellek kullanıyor (yeni embed yok)", stats2["new"] == 0)

    # Arama çalışıyor
    qvec, _ = embeddings.embed_text("sandalye al", force_mock=True)
    results = store.search(qvec, top_k=3)
    check("Arama sonuç döndürüyor", len(results) > 0)
    check("Sonuçlar skor içeriyor", all("score" in r for r in results))

    # Skora göre azalan sıralı mı
    scores = [r["score"] for r in results]
    check("Sonuçlar skora göre azalan sıralı", scores == sorted(scores, reverse=True))


# ---------------------------------------------------------------------------
# 3. Retriever testleri
# ---------------------------------------------------------------------------
def test_retriever():
    print("\n[3] Retriever")
    from rag.retriever import PolicyRetriever, get_retriever, reset_retriever

    retriever = PolicyRetriever(index_path=None, force_mock=True)
    retriever.ensure_index()

    # Temel geri getirme
    r = retriever.retrieve("10.000 TL sandalye al, elektronik alma",
                           company_id="tekno_a")
    check("RAG bağlam buluyor", r["used"] is True)
    check("Bağlam metni boş değil", len(r["context_text"]) > 0)
    check("Kaynak listesi dönüyor", len(r["sources"]) > 0)

    # ŞİRKET İZOLASYONU — en kritik test
    r_ret = retriever.retrieve("temizlik malzemesi al", company_id="perakende_b")
    leaked = any(s["company_id"] == "tekno_a" for s in r_ret["sources"])
    check("Şirket izolasyonu: başka şirketin dokümanı sızmıyor", not leaked)

    # Determinizm
    ra = retriever.retrieve("sandalye al", company_id="tekno_a")
    rb = retriever.retrieve("sandalye al", company_id="tekno_a")
    check("Retrieval deterministik",
          [s["doc_id"] for s in ra["sources"]] ==
          [s["doc_id"] for s in rb["sources"]])

    # company_id None → yine çalışır (genel arama)
    r_none = retriever.retrieve("yüksek tutarlı işlem", company_id=None)
    check("Şirketsiz sorgu da çalışır", "used" in r_none)

    # Singleton
    reset_retriever()
    s1 = get_retriever(force_mock=True)
    s2 = get_retriever(force_mock=True)
    check("Singleton tek örnek döndürür", s1 is s2)
    reset_retriever()


# ---------------------------------------------------------------------------
# 4. Entegrasyon testleri (parse_intent geriye uyumluluğu)
# ---------------------------------------------------------------------------
def test_integration():
    print("\n[4] intent_parser Entegrasyonu")
    _install_schema_mock()

    # OPENAI_API_KEY'i temizle → deterministik (LLM/RAG devreye girmez)
    saved_key = os.environ.pop("OPENAI_API_KEY", None)
    try:
        import importlib
        if "intent_parser" in sys.modules:
            importlib.reload(sys.modules["intent_parser"])
        from services import intent_parser

        # GERİYE UYUMLULUK: eski 2-argümanlı çağrı çalışmalı
        r = intent_parser.parse_intent("5000 TL ofis sandalyesi al", "user_1")
        check("Eski çağrı imzası (2 argüman) çalışıyor", "mandate" in r)
        check("Anahtar yokken rule modu", r["parse_mode"] == "rule")
        check("policy_sources her zaman var (boş liste)",
              r.get("policy_sources") == [])

        # company_id verilse de, anahtar yoksa RAG devreye girmez
        r2 = intent_parser.parse_intent("5000 TL sandalye", "u2",
                                        company_id="tekno_a")
        check("Anahtar yokken company_id verilse de rule modu",
              r2["parse_mode"] == "rule")

        # _get_policy_context güvenli (None → boş)
        ctx = intent_parser._get_policy_context("test", None)
        check("_get_policy_context(None) güvenli boş döner",
              ctx["context_text"] == "" and ctx["sources"] == [])

        # Mandate alanları eksiksiz
        m = r["mandate"]
        required = ["max_amount", "total_limit", "allowed_categories",
                    "blocked_categories", "status"]
        check("Mandate alanları eksiksiz",
              all(f in m for f in required))

    finally:
        # Anahtarı geri koy (diğer testleri etkilememek için)
        if saved_key is not None:
            os.environ["OPENAI_API_KEY"] = saved_key


# ---------------------------------------------------------------------------
# Ana çalıştırıcı
# ---------------------------------------------------------------------------
def run_all():
    print("=" * 64)
    print("  IntentPay AI — RAG Test Paketi (deterministik, mock embedding)")
    print("=" * 64)

    # RAG testleri için OPENAI_API_KEY'i temizle — testler mock kullanır,
    # gerçek API'ye gitmemeli (determinizm + maliyet).
    saved_key = os.environ.pop("OPENAI_API_KEY", None)
    try:
        test_documents()
        test_embeddings()
        test_retriever()
        test_integration()
    finally:
        if saved_key is not None:
            os.environ["OPENAI_API_KEY"] = saved_key

    print("\n" + "=" * 64)
    if _FAIL == 0:
        print(f"  SONUÇ: {_PASS}/{_PASS} test geçti \u2713")
    else:
        print(f"  SONUÇ: {_PASS} geçti, {_FAIL} BAŞARISIZ")
        print(f"  Başarısız: {', '.join(_FAILURES)}")
    print("=" * 64)
    return _FAIL == 0


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)