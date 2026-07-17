"""
IntentPay AI — RAG Canlı Demo (Gerçek OpenAI Embedding)
========================================================

Bu script, RAG'ı GERÇEK OpenAI embedding ile çalıştırır ve bir talimatın
şirket politikalarıyla nasıl zenginleştiğini gösterir.

ÖNKOŞUL: .env dosyasında geçerli OPENAI_API_KEY olmalı.
         (Yoksa mock'a düşer ve uyarı verir.)

Çalıştırma:
    cd backend
    python rag_canli_demo.py

Bu, test değil bir GÖSTERİM aracıdır — RAG'ın gerçek gücünü görmek için.
"""
from __future__ import annotations
import os
import sys

# .env yükle (proje kökünden) — mevcut _load_dotenv mantığına benzer basit yükleyici
def _load_env():
    for path in [".env", "../.env"]:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        os.environ.setdefault(k.strip(), v.strip())
            return True
    return False


def main():
    _load_env()

    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    print("=" * 70)
    print("  IntentPay AI — RAG Canlı Demo")
    print("=" * 70)
    print(f"  OpenAI anahtarı: {'VAR (gerçek embedding)' if has_key else 'YOK (mock)'}")
    if not has_key:
        print("  UYARI: Anahtar yok. Gerçek gücü görmek için .env'e OPENAI_API_KEY ekleyin.")
    print("=" * 70)

    from rag.retriever import PolicyRetriever

    # Gerçek embedding kullan (force_mock=False) — anahtar varsa OpenAI'ye gider
    retriever = PolicyRetriever(index_path=None, force_mock=not has_key)
    stats = retriever.ensure_index()
    print(f"\n  İndeks hazır: {stats['document_count']} doküman")
    print(f"  Embedding modu: {stats.get('mode_counts', {})}\n")

    # --- Senaryo: Kullanıcı politikadan daha yüksek limit istiyor ---
    senaryolar = [
        {
            "company": "tekno_a",
            "company_name": "Tekno A.Ş.",
            "text": "Bu ay için 25.000 TL'ye kadar ofis sandalyesi ve masa al, "
                    "bir de birkaç laptop lazım",
            "aciklama": "Kullanıcı 25.000 istiyor ve laptop (elektronik) istiyor. "
                        "Tekno A.Ş. politikası: tek işlem 10.000'i aşamaz, "
                        "elektronik ek onay ister. RAG bunu yakalamalı.",
        },
        {
            "company": "perakende_b",
            "company_name": "Perakende B",
            "text": "Şube için 8.000 TL temizlik malzemesi al",
            "aciklama": "Perakende B politikası: tek işlem 5.000'i aşamaz. "
                        "8.000 istendi — RAG bunu bölge müdürü onayına "
                        "bağlamalı.",
        },
    ]

    for i, s in enumerate(senaryolar, 1):
        print("\n" + "─" * 70)
        print(f"  SENARYO {i}: {s['company_name']}")
        print("─" * 70)
        print(f"  Talimat: \"{s['text']}\"")
        print(f"\n  Beklenti: {s['aciklama']}")

        result = retriever.retrieve(s["text"], company_id=s["company"])

        print(f"\n  → RAG bağlam buldu mu: {result['used']}")
        print(f"  → Çekilen politika dokümanları (en alakalı):")
        for src in result["sources"]:
            print(f"      • {src['title']}  [{src['category']}]  "
                  f"(benzerlik: {src['score']})")
            print(f"          kaynak: {src['source']}")

        print(f"\n  → LLM'e verilecek bağlam (bu, mandate'i şekillendirecek):")
        ctx = result["context_text"]
        # İlk 400 karakteri göster
        preview = ctx[:400].replace("\n", "\n      ")
        print(f"      {preview}...")

    print("\n" + "=" * 70)
    if has_key:
        print("  Bu bağlam intent_parser'a geçtiğinde, LLM kullanıcının talebini")
        print("  BU politikalarla birlikte değerlendirir ve politika-farkında bir")
        print("  mandate üretir. İşte RAG'ın gücü: 'parser' → 'politika asistanı'.")
    else:
        print("  NOT: Mock embedding ile çalıştı. Gerçek OpenAI ile benzerlik")
        print("  skorları çok daha yüksek ve isabetli olur.")
    print("=" * 70)


if __name__ == "__main__":
    main()