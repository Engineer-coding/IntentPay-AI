#!/usr/bin/env bash
# IntentPay AI - Tek komutla başlatma
# Backend + Frontend tek sunucudan servis edilir.
set -e
cd "$(dirname "$0")/backend"

echo "▸ IntentPay AI başlatılıyor..."

# (Opsiyonel) risk modeli yoksa eğit
if [ ! -f data/risk_model.json ]; then
  echo "▸ Risk modeli bulunamadı, eğitiliyor (XGBoost varsa)..."
  python train_risk_model.py || echo "  (ML kütüphaneleri yok; heuristic fallback kullanılacak)"
fi

PORT="${PORT:-8787}"
echo "▸ Sunucu hazır:  http://localhost:${PORT}"
echo "▸ Tarayıcıda bu adresi açın. Durdurmak için Ctrl+C."
PORT="$PORT" python server.py