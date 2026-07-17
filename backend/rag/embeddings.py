"""
IntentPay AI — RAG Embedding & Vektör Deposu
=============================================

Bu modül RAG'ın "kalbi"dir: politika dokümanlarını sayısal vektörlere
(embedding) çevirir, bunları kalıcı bir depoda saklar ve benzerlik
araması için hazır tutar.

Tasarım kararları:
- EMBEDDING: OpenAI text-embedding-3-small (ucuz, hızlı, 1536 boyut).
- ARAMA: numpy ile kosinüs benzerliği. FAISS gibi ağır bir bağımlılık YOK;
  bu ölçekte (yüzlerce vektör) brute-force arama milisaniyede döner.
- KALICILIK: Embedding'ler bir kez hesaplanıp JSON'da saklanır. Aynı
  dokümanı tekrar tekrar embed etmeyiz (maliyet + hız). İçerik değişirse
  doc_id değişir (documents.py'deki deterministik hash sayesinde) ve yeniden
  embed edilir.
- DETERMİNİZM: Test ortamında (veya anahtar yokken) gerçek OpenAI yerine
  deterministik bir "mock embedding" kullanılır. Böylece testler internet ve
  API anahtarı gerektirmez, her seferinde aynı sonucu verir → 23 test bozulmaz.
- FALLBACK: Anahtar yoksa sistem çökmez; deterministik mock ile çalışır.

Bağımlılık: yalnızca numpy (zaten proje bağımlılıklarında). OpenAI çağrısı
standart urllib ile yapılır — ekstra paket gerektirmez.
"""

from __future__ import annotations
import os
import json
import hashlib
import urllib.request
import urllib.error
from typing import List, Dict, Optional, Tuple

import numpy as np

# Aynı klasördeki documents modülünü içe aktar.
try:
    from .documents import PolicyDocument, get_all_documents
except ImportError:
    # Doğrudan çalıştırıldığında (python embeddings.py) göreli import çalışmaz.
    from documents import PolicyDocument, get_all_documents


# ----------------------------------------------------------------------------
# Yapılandırma
# ----------------------------------------------------------------------------

_EMBED_MODEL = os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small")
_EMBED_DIM = 1536  # text-embedding-3-small boyutu
_MOCK_DIM = 256    # mock embedding boyutu (test için, küçük ve hızlı)


# ----------------------------------------------------------------------------
# Embedding üretimi
# ----------------------------------------------------------------------------

def _mock_embedding(text: str, dim: int = _MOCK_DIM) -> List[float]:
    """
    Deterministik sahte embedding — test ve fallback için.

    Metnin hash'inden tohumlanmış bir rastgele vektör üretir. Aynı metin HER
    ZAMAN aynı vektörü verir (determinizm). Anlamsal benzerlik taşımaz ama
    kelime örtüşmesi olan metinler bir miktar yakınlaşsın diye, metindeki
    kelimeleri de hash'e katarız — böylece "sandalye" içeren iki metin,
    tamamen alakasız iki metinden biraz daha yakın çıkar.

    Bu, gerçek embedding kadar iyi değildir ama testlerin deterministik ve
    anlamlı olması için yeterlidir.
    """
    # Kelime bazlı kaba bir anlamsal sinyal: her kelime bir boyuta katkı yapar.
    vec = np.zeros(dim, dtype=np.float64)
    words = _normalize(text).split()
    for w in words:
        h = int(hashlib.sha1(w.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        vec[idx] += 1.0
    # Genel bir tohumla küçük gürültü ekle (tamamen boş vektörleri önlemek için)
    seed = int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    vec += rng.normal(0, 0.01, dim)
    # Normalize et (kosinüs için birim vektör)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


def _openai_embedding(text: str) -> List[float]:
    """
    OpenAI embedding API'sinden gerçek embedding alır.

    Hata durumunda RuntimeError fırlatır (görünür hata — sessiz kalmaz).
    Çağıran taraf, hata durumunda mock'a düşmeyi tercih edebilir.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY yok")

    payload = json.dumps({
        "model": _EMBED_MODEL,
        "input": text,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=payload,
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"OpenAI embedding API {e.code}: {detail}")
    except Exception as e:
        raise RuntimeError(f"OpenAI embedding çağrısı başarısız: {e}")

    return body["data"][0]["embedding"]


def embed_text(text: str, force_mock: bool = False) -> Tuple[List[float], str]:
    """
    Bir metni embedding'e çevirir.

    Dönüş: (vektör, mod) — mod = "openai" | "mock".

    Karar mantığı:
      - force_mock=True veya OPENAI_API_KEY yoksa → mock (deterministik).
      - Aksi halde → OpenAI dener; başarısız olursa mock'a düşer (fallback).

    Bu, LLM parser'daki fallback mantığının aynısıdır: gerçek varsa gerçek,
    yoksa/hata olursa deterministik yedek. Sistem hiçbir durumda çökmez.
    """
    if force_mock or not os.environ.get("OPENAI_API_KEY"):
        return _mock_embedding(text), "mock"
    try:
        return _openai_embedding(text), "openai"
    except Exception as e:
        print(f"[rag.embeddings] OpenAI embedding başarısız, mock'a düşülüyor: {e}")
        return _mock_embedding(text), "mock"


def _normalize(text: str) -> str:
    """Basit metin normalizasyonu — küçük harf, Türkçe uyumlu."""
    return text.lower().strip()


# ----------------------------------------------------------------------------
# Vektör Deposu (kalıcı)
# ----------------------------------------------------------------------------

class VectorStore:
    """
    Doküman embedding'lerini tutan kalıcı vektör deposu.

    Yapı: her kayıt = {doc_id, company_id, category, vector, mode}.
    Vektörler JSON'da saklanır (proje persistence mantığıyla uyumlu).

    Kullanım:
        store = VectorStore("data/rag_index.json")
        store.build(get_all_documents())   # bir kez indeksle
        results = store.search(query_vec, top_k=3)
    """

    def __init__(self, index_path: Optional[str] = None):
        self.index_path = index_path
        self.records: List[Dict] = []
        self._matrix: Optional[np.ndarray] = None  # (N, dim) hızlı arama için
        if index_path and os.path.exists(index_path):
            self.load()

    # ----- İnşa ve güncelleme -----

    def build(self, documents: List[PolicyDocument],
              force_mock: bool = False) -> Dict:
        """
        Doküman listesini embed edip indeksi (yeniden) kurar.

        Zaten indekste olan (aynı doc_id) dokümanlar yeniden embed EDİLMEZ —
        sadece yeni veya değişmiş olanlar. Bu, maliyeti ve süreyi düşürür.

        Dönüş: özet istatistik {total, new, cached, mode_counts}.
        """
        existing = {r["doc_id"]: r for r in self.records}
        new_records: List[Dict] = []
        stats = {"total": len(documents), "new": 0, "cached": 0,
                 "mode_counts": {"openai": 0, "mock": 0}}

        for doc in documents:
            if doc.doc_id in existing:
                # Zaten indekste — yeniden embed etme.
                new_records.append(existing[doc.doc_id])
                stats["cached"] += 1
                mode = existing[doc.doc_id].get("mode", "mock")
                stats["mode_counts"][mode] = stats["mode_counts"].get(mode, 0) + 1
                continue
            # Yeni doküman — embed et. Başlık + içerik birlikte embed edilir
            # (başlık da anlamsal sinyal taşır).
            text = f"{doc.title}\n{doc.content}"
            vec, mode = embed_text(text, force_mock=force_mock)
            new_records.append({
                "doc_id": doc.doc_id,
                "company_id": doc.company_id,
                "category": doc.category,
                "title": doc.title,
                "source": doc.source,
                "vector": vec,
                "mode": mode,
            })
            stats["new"] += 1
            stats["mode_counts"][mode] = stats["mode_counts"].get(mode, 0) + 1

        self.records = new_records
        self._rebuild_matrix()

        # MOD TUTARLILIĞI KONTROLÜ: Tüm vektörler aynı boyutta olmalı.
        # Eğer önbellekte farklı modda (mock/gerçek) vektörler karışmışsa,
        # bu bir tutarsızlıktır. Boyutları kontrol et; karışıksa temiz bir
        # yeniden inşa öner.
        dims = {len(r["vector"]) for r in self.records}
        if len(dims) > 1:
            print(f"[rag.embeddings] UYARI: İndekste karışık boyutlar var "
                  f"{dims}. Bu, mock ve gerçek embedding'in karıştığı anlamına "
                  f"gelir. Tutarlı sonuç için indeksi tek modda yeniden kurun "
                  f"(rag_index.json'u silip yeniden başlatın).")

        if self.index_path:
            self.save()
        return stats

    def _rebuild_matrix(self):
        """Hızlı arama için vektörleri tek bir numpy matrisine yığar."""
        if not self.records:
            self._matrix = None
            return
        # Boyut tutarlılığını kontrol et — karışık boyut varsa numpy object
        # array üretir ve matmul çöker. Bunu önlemek için sadece tutarlıysa
        # matris kur.
        dims = {len(r["vector"]) for r in self.records}
        if len(dims) > 1:
            # Karışık boyut — matris kurma, search boyut kontrolüyle boş dönecek.
            self._matrix = None
            return
        self._matrix = np.array([r["vector"] for r in self.records],
                                dtype=np.float64)

    # ----- Arama -----

    def search(self, query_vector: List[float], top_k: int = 3,
               company_id: Optional[str] = None,
               include_regulation: bool = True) -> List[Dict]:
        """
        Sorgu vektörüne en benzer top_k dokümanı döndürür (kosinüs benzerliği).

        company_id verilirse, sadece o şirketin (+ isteğe bağlı ortak
        regülasyon) dokümanları aranır. Bu, "her müşteri kendi politikalarıyla"
        prensibini uygular.

        Dönüş: [{doc_id, company_id, category, title, source, score}, ...]
        skora göre azalan sırada.
        """
        if self._matrix is None or not self.records:
            return []

        # Şirket filtresi (istenirse)
        if company_id is not None:
            allowed_idx = [
                i for i, r in enumerate(self.records)
                if r["company_id"] == company_id
                or (include_regulation and r["company_id"] == "_regulation_tr")
            ]
        else:
            allowed_idx = list(range(len(self.records)))

        if not allowed_idx:
            return []

        q = np.array(query_vector, dtype=np.float64)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []
        q = q / q_norm

        # BOYUT KONTROLÜ: Sorgu vektörü ile indeks vektörleri aynı boyutta
        # olmalı. Farklıysa (ör. indeks mock=256, sorgu gerçek=1536), anlamlı
        # bir uyarı ver ve boş dön — çökme yerine. Bu durum, indeks bir modda
        # (mock/gerçek) kurulup sorgunun başka modda yapılmasından kaynaklanır;
        # çözüm indeksi yeniden kurmaktır (build).
        index_dim = self._matrix.shape[1]
        if q.shape[0] != index_dim:
            print(f"[rag.embeddings] UYARI: Boyut uyuşmazlığı — "
                  f"indeks {index_dim}D, sorgu {q.shape[0]}D. "
                  f"İndeks farklı bir embedding modunda kurulmuş. "
                  f"İndeksi yeniden kurun (rag_index.json'u silin veya "
                  f"ensure_index(rebuild=True) çağırın).")
            return []

        # Kosinüs benzerliği: vektörler zaten normalize (build sırasında),
        # ama garanti için tekrar normalize ederek nokta çarpımı alıyoruz.
        sub_matrix = self._matrix[allowed_idx]
        sub_norms = np.linalg.norm(sub_matrix, axis=1, keepdims=True)
        sub_norms[sub_norms == 0] = 1.0
        sub_normalized = sub_matrix / sub_norms
        scores = sub_normalized @ q  # (M,)

        # top_k seç
        k = min(top_k, len(allowed_idx))
        top_local = np.argsort(-scores)[:k]

        results = []
        for local_i in top_local:
            global_i = allowed_idx[int(local_i)]
            r = self.records[global_i]
            results.append({
                "doc_id": r["doc_id"],
                "company_id": r["company_id"],
                "category": r["category"],
                "title": r["title"],
                "source": r["source"],
                "score": float(scores[int(local_i)]),
            })
        return results

    # ----- Kalıcılık -----

    def save(self):
        """İndeksi diske yazar (JSON)."""
        if not self.index_path:
            return
        os.makedirs(os.path.dirname(self.index_path) or ".", exist_ok=True)
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump({"records": self.records}, f, ensure_ascii=False)

    def load(self):
        """İndeksi diskten okur."""
        with open(self.index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.records = data.get("records", [])
        self._rebuild_matrix()

    def stats(self) -> Dict:
        """İndeks durumu özeti."""
        modes = {}
        for r in self.records:
            m = r.get("mode", "mock")
            modes[m] = modes.get(m, 0) + 1
        return {
            "document_count": len(self.records),
            "mode_counts": modes,
            "index_path": self.index_path,
        }


# ----------------------------------------------------------------------------
# Kolaylık fonksiyonu: standart indeks kur
# ----------------------------------------------------------------------------

def build_default_index(index_path: Optional[str] = None,
                        force_mock: bool = False) -> VectorStore:
    """Tüm demo dokümanlarını indeksleyip hazır bir VectorStore döndürür."""
    store = VectorStore(index_path)
    stats = store.build(get_all_documents(), force_mock=force_mock)
    return store


# ----------------------------------------------------------------------------
# Kendi kendini test
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== RAG Embedding & Vektör Deposu — Kendi Kendine Test ===\n")
    print("(Test deterministik mock embedding kullanır — internet gerekmez)\n")

    # Mock ile indeks kur (deterministik)
    store = VectorStore(index_path=None)
    stats = store.build(get_all_documents(), force_mock=True)
    print(f"İndeks kuruldu: {stats['total']} doküman "
          f"({stats['new']} yeni, {stats['cached']} önbellek)")
    print(f"Mod dağılımı: {stats['mode_counts']}")

    # Determinizm testi: aynı metin iki kez → aynı vektör
    v1, _ = embed_text("ofis sandalyesi al", force_mock=True)
    v2, _ = embed_text("ofis sandalyesi al", force_mock=True)
    assert v1 == v2, "Mock embedding deterministik olmalı!"
    print("✓ Mock embedding deterministik (aynı metin → aynı vektör)")

    # Arama testi: "elektronik alma" sorgusu → elektronik/kategori dokümanları
    print("\n--- Arama testi ---")
    query = "Bu hafta 10.000 TL'ye ofis sandalyesi al, elektronik alma"
    qvec, _ = embed_text(query, force_mock=True)
    results = store.search(qvec, top_k=3, company_id="tekno_a")
    print(f"Sorgu: {query!r}")
    print(f"Tekno A.Ş. için en alakalı {len(results)} doküman:")
    for r in results:
        print(f"  • [{r['category']}] {r['title']}  (skor: {r['score']:.3f})")
        print(f"      kaynak: {r['source']}")

    # Şirket filtresi testi: perakende sorgusu perakende dokümanı getirmeli
    print("\n--- Şirket filtresi testi ---")
    results_r = store.search(qvec, top_k=2, company_id="perakende_b")
    companies_in_result = {r["company_id"] for r in results_r}
    # Sadece perakende_b veya regülasyon gelmeli, tekno_a gelmemeli
    assert "tekno_a" not in companies_in_result, \
        "Şirket filtresi çalışmıyor — başka şirketin dokümanı geldi!"
    print(f"✓ Perakende B araması sadece kendi + regülasyon dokümanı getirdi")
    print(f"  (gelen şirketler: {companies_in_result})")

    # Kalıcılık testi
    print("\n--- Kalıcılık testi ---")
    import tempfile
    tmp = tempfile.mktemp(suffix=".json")
    store2 = VectorStore(index_path=tmp)
    store2.build(get_all_documents(), force_mock=True)
    store3 = VectorStore(index_path=tmp)  # diskten yükle
    assert len(store3.records) == len(store2.records), "Kalıcılık bozuk!"
    r2 = store2.search(qvec, top_k=1, company_id="tekno_a")
    r3 = store3.search(qvec, top_k=1, company_id="tekno_a")
    assert r2[0]["doc_id"] == r3[0]["doc_id"], "Yüklenen indeks farklı sonuç verdi!"
    print(f"✓ İndeks diske yazılıp geri yüklendi, sonuçlar tutarlı")
    os.remove(tmp)

    # Önbellek testi: ikinci build yeni embed yapmamalı
    print("\n--- Önbellek testi ---")
    stats2 = store.build(get_all_documents(), force_mock=True)
    assert stats2["new"] == 0, "İkinci build'de yeni embed olmamalı (önbellek)!"
    print(f"✓ İkinci indeksleme önbellek kullandı ({stats2['cached']} önbellek, "
          f"{stats2['new']} yeni)")

    print("\n=== Tüm testler başarılı ===")