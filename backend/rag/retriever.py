"""
IntentPay AI — RAG Retriever
=============================

Bu modül, RAG'ın "geri getirme" (retrieval) katmanıdır. Adım 2'deki embedding
ve vektör deposunu bir araya getirip, üst katmanların (intent_parser) tek bir
temiz fonksiyonla kullanabileceği bir arayüz sunar:

    kullanıcı talimatı + şirket → alakalı politika bağlamı (LLM'e hazır metin)

Sorumlulukları:
1. Vektör indeksini bir kez kurup bellekte tutmak (her sorguda yeniden kurma).
2. Gelen sorguyu embed edip, ilgili şirketin dokümanları içinde aramak.
3. Bulunan doküman parçalarını, LLM prompt'una eklenebilecek düzenli bir
   bağlam metnine ("context block") çevirmek.
4. Hangi dokümanların kullanıldığını (kaynak/başlık) döndürmek — arayüzde
   "bu mandate şu politikalardan etkilendi" göstermek için.

Tasarım:
- Retriever bir SINGLETON gibi çalışır (get_retriever()), böylece indeks
  uygulama ömrü boyunca bir kez kurulur.
- Bağlam metni, doküman içeriğinin TAMAMINI değil, LLM'e yetecek özü taşır.
- Skor eşiği (min_score) ile alakasız dokümanlar elenir — gürültüyü önler.
- Her şey opsiyoneldir: RAG kapalıysa boş bağlam döner, sistem eskisi gibi
  çalışır.
"""

from __future__ import annotations
import os
from typing import List, Dict, Optional

try:
    from .documents import get_all_documents, get_document_by_id
    from .embeddings import VectorStore, embed_text, build_default_index
except ImportError:
    from documents import get_all_documents, get_document_by_id
    from embeddings import VectorStore, embed_text, build_default_index


# ----------------------------------------------------------------------------
# Yapılandırma
# ----------------------------------------------------------------------------

# İndeks dosyasının varsayılan konumu (proje data/ klasörü mantığıyla uyumlu).
_DEFAULT_INDEX_PATH = os.environ.get(
    "RAG_INDEX_PATH",
    os.path.join("data", "rag_index.json"),
)

# Kaç doküman parçası çekilsin (top-k).
_DEFAULT_TOP_K = int(os.environ.get("RAG_TOP_K", "3"))

# Bu skorun altındaki dokümanlar "alakasız" sayılıp elenir.
# NOT: mock embedding'de skorlar düşüktür (~0.2), gerçek OpenAI'de yüksektir
# (~0.4-0.7). Eşiği düşük tutuyoruz ki mock'ta da bir şeyler dönsün; gerçek
# embedding'de zaten alakasızlar doğal olarak elenir.
_MIN_SCORE = float(os.environ.get("RAG_MIN_SCORE", "0.05"))


# ----------------------------------------------------------------------------
# Retriever
# ----------------------------------------------------------------------------

class PolicyRetriever:
    """
    Politika dokümanlarını geri getiren ana sınıf.

    Kullanım:
        retriever = PolicyRetriever()
        retriever.ensure_index()                 # bir kez
        result = retriever.retrieve(
            user_text="10.000 TL sandalye al",
            company_id="tekno_a",
        )
        # result["context_text"]  → LLM'e eklenecek metin
        # result["sources"]       → arayüzde gösterilecek kaynaklar
    """

    def __init__(self, index_path: Optional[str] = None,
                 force_mock: bool = False):
        self.index_path = index_path if index_path is not None else _DEFAULT_INDEX_PATH
        self.force_mock = force_mock
        self.store: Optional[VectorStore] = None

    # ----- İndeks yönetimi -----

    def ensure_index(self, rebuild: bool = False) -> Dict:
        """
        İndeksin hazır olduğundan emin olur. Yoksa kurar.

        rebuild=True ile zorla yeniden kurar (dokümanlar değiştiyse).

        AKILLI YENİDEN KURMA: Diskten yüklenen indeksin embedding modu (mock/
        gerçek), şu an kullanılacak moddan farklıysa, indeks otomatik yeniden
        kurulur. Bu, mock↔gerçek geçişindeki boyut uyuşmazlığını (256 vs 1536)
        otomatik çözer — kullanıcının elle dosya silmesine gerek kalmaz.

        Dönüş: indeks istatistikleri (stats() formatında — tutarlı).
        """
        if self.store is not None and not rebuild:
            return self.store.stats()

        # İndeks dosyası varsa yükler, yoksa kurar.
        self.store = VectorStore(self.index_path)

        # Diskten yüklenen indeksin modu, istenen modla uyuşuyor mu?
        # (force_mock=True → "mock" bekleriz; False → "openai" bekleriz)
        needs_rebuild = rebuild or not self.store.records
        if self.store.records and not needs_rebuild:
            existing_modes = {r.get("mode", "mock") for r in self.store.records}
            expected = "mock" if self.force_mock else "openai"
            # Beklenen mod indekste yoksa (ör. gerçek istiyoruz ama indeks mock),
            # veya indekste karışık mod varsa → yeniden kur.
            if expected not in existing_modes or len(existing_modes) > 1:
                # force_mock=False iken indeks "mock" ise: gerçek anahtar artık
                # devrede olabilir, gerçek embedding'le yeniden kur.
                # force_mock=True iken indeks "openai" ise: test/mock istiyoruz.
                needs_rebuild = True

        if needs_rebuild:
            self.store.build(get_all_documents(), force_mock=self.force_mock)
        return self.store.stats()

    # ----- Ana geri getirme -----

    def retrieve(self, user_text: str, company_id: Optional[str] = None,
                 top_k: int = _DEFAULT_TOP_K,
                 min_score: float = _MIN_SCORE) -> Dict:
        """
        Kullanıcı talimatına en alakalı politika dokümanlarını getirir ve
        LLM'e verilecek bağlam metnini oluşturur.

        Dönüş:
            {
              "context_text": str,     # LLM prompt'una eklenecek metin
              "sources": [             # arayüz için (mandate şundan etkilendi)
                  {"title", "source", "category", "company_id", "score"}, ...
              ],
              "used": bool,            # RAG gerçekten bağlam buldu mu
              "company_id": str | None,
            }
        """
        self.ensure_index()

        # Sorguyu embed et (mock veya gerçek — force_mock'a göre)
        qvec, _mode = embed_text(user_text, force_mock=self.force_mock)

        # Ara
        hits = self.store.search(
            qvec, top_k=top_k, company_id=company_id, include_regulation=True
        )

        # Skor eşiğiyle ele
        hits = [h for h in hits if h["score"] >= min_score]

        if not hits:
            return {
                "context_text": "",
                "sources": [],
                "used": False,
                "company_id": company_id,
            }

        # Bağlam metnini kur — her doküman parçasının başlığı, içeriği, kaynağı
        context_text = self._build_context_text(hits)

        # Arayüz için kaynak listesi
        sources = [
            {
                "doc_id": h["doc_id"],
                "title": h["title"],
                "source": h["source"],
                "category": h["category"],
                "company_id": h["company_id"],
                "score": round(h["score"], 3),
            }
            for h in hits
        ]

        return {
            "context_text": context_text,
            "sources": sources,
            "used": True,
            "company_id": company_id,
        }

    def _build_context_text(self, hits: List[Dict]) -> str:
        """
        Bulunan dokümanları LLM'e verilecek düzenli bir metne çevirir.

        Her doküman için başlık + tam içerik + kaynak eklenir. İçerik
        documents.py'den doc_id ile çekilir (arama sonucu sadece meta taşır).
        """
        parts: List[str] = []
        parts.append("İlgili şirket politikaları ve regülasyon kuralları:")
        for i, h in enumerate(hits, 1):
            doc = get_document_by_id(h["doc_id"])
            if not doc:
                continue
            parts.append(
                f"\n[{i}] {doc.title} (Kaynak: {doc.source})\n{doc.content}"
            )
        return "\n".join(parts)


# ----------------------------------------------------------------------------
# Singleton erişimi
# ----------------------------------------------------------------------------

_retriever_singleton: Optional[PolicyRetriever] = None


def get_retriever(force_mock: bool = False) -> PolicyRetriever:
    """
    Uygulama genelinde tek bir retriever örneği döndürür (singleton).
    İndeks bir kez kurulur, tekrar tekrar değil.
    """
    global _retriever_singleton
    if _retriever_singleton is None:
        _retriever_singleton = PolicyRetriever(force_mock=force_mock)
        _retriever_singleton.ensure_index()
    return _retriever_singleton


def reset_retriever():
    """Test için singleton'ı sıfırlar."""
    global _retriever_singleton
    _retriever_singleton = None


# ----------------------------------------------------------------------------
# Kendi kendini test
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== RAG Retriever — Kendi Kendine Test ===\n")
    print("(Deterministik mock embedding ile — internet gerekmez)\n")

    # Bellekte çalışan retriever (dosyaya yazmadan)
    retriever = PolicyRetriever(index_path=None, force_mock=True)
    stats = retriever.ensure_index()
    print(f"İndeks hazır: {stats['document_count']} doküman\n")

    # Senaryo 1: Tekno A.Ş. için ofis + elektronik talimatı
    print("--- Senaryo 1: Tekno A.Ş. ---")
    q1 = "Bu hafta 10.000 TL'ye kadar ofis sandalyesi al, elektronik alma"
    r1 = retriever.retrieve(q1, company_id="tekno_a")
    print(f"Talimat: {q1!r}")
    print(f"RAG bağlam buldu mu: {r1['used']}")
    print(f"Kullanılan kaynaklar ({len(r1['sources'])}):")
    for s in r1["sources"]:
        print(f"  • {s['title']}  [{s['category']}]  skor={s['score']}")
        print(f"      {s['source']}")
    print(f"\nLLM'e verilecek bağlam metni (ilk 300 karakter):")
    print("  " + r1["context_text"][:300].replace("\n", "\n  ") + "...")

    # Senaryo 2: Perakende B için temizlik talimatı
    print("\n\n--- Senaryo 2: Perakende B ---")
    q2 = "Şube için 3.000 TL temizlik malzemesi al"
    r2 = retriever.retrieve(q2, company_id="perakende_b")
    print(f"Talimat: {q2!r}")
    print(f"Kullanılan kaynaklar:")
    for s in r2["sources"]:
        print(f"  • {s['title']}  [{s['company_id']}]  skor={s['score']}")

    # Doğrulama: Perakende sorgusunda tekno_a dokümanı OLMAMALI
    tekno_leaked = any(s["company_id"] == "tekno_a" for s in r2["sources"])
    assert not tekno_leaked, "İzolasyon bozuk — başka şirketin dokümanı sızdı!"
    print("✓ Şirket izolasyonu korundu (tekno_a dokümanı sızmadı)")

    # Senaryo 3: Şirket seçilmeden (genel)
    print("\n--- Senaryo 3: Şirket belirtilmeden ---")
    r3 = retriever.retrieve("yüksek tutarlı işlem onayı", company_id=None)
    print(f"Bağlam bulundu: {r3['used']}, kaynak sayısı: {len(r3['sources'])}")

    # Determinizm: aynı sorgu iki kez → aynı sonuç
    ra = retriever.retrieve(q1, company_id="tekno_a")
    rb = retriever.retrieve(q1, company_id="tekno_a")
    assert [s["doc_id"] for s in ra["sources"]] == \
           [s["doc_id"] for s in rb["sources"]], "Sonuç deterministik değil!"
    print("\n✓ Retrieval deterministik (aynı sorgu → aynı sonuç)")

    # Singleton testi
    reset_retriever()
    s1 = get_retriever(force_mock=True)
    s2 = get_retriever(force_mock=True)
    assert s1 is s2, "Singleton çalışmıyor!"
    print("✓ Singleton çalışıyor (tek örnek)")

    print("\n=== Tüm testler başarılı ===")