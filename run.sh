#!/usr/bin/env bash
# Load local environment variables if .env exists.
if [ -f ".env" ]; then
  set -a
  . ./.env
  set +a
fi

# IntentPay AI - Tek komutla başlatma
# Backend + Frontend tek sunucudan servis edilir.
#
# Kullanım:
#   ./run.sh           -> sunucuyu başlat (kalıcı veriyle)
#   ./run.sh test      -> otomatik test paketini çalıştır
#   ./run.sh fresh     -> veritabanını sıfırlayıp başlat
set -e
cd "$(dirname "$0")/backend"

# Test modu
if [ "$1" = "test" ]; then
  echo "▸ Otomatik test paketi çalıştırılıyor..."
  python run_tests.py
  exit $?
fi

echo "▸ IntentPay AI başlatılıyor..."

# (Opsiyonel) risk modeli yoksa eğit
if [ ! -f data/risk_model.json ]; then
  echo "▸ Risk modeli bulunamadı, eğitiliyor (XGBoost varsa)..."
  python train_risk_model.py || echo "  (ML kütüphaneleri yok; heuristic fallback kullanılacak)"
fi

# Veritabanını sıfırla (fresh modu)
if [ "$1" = "fresh" ]; then
  export RESET_DB=1
  echo "▸ Veritabanı sıfırlanıyor (temiz başlangıç)..."
fi

PORT="${PORT:-8787}"
echo "▸ Sunucu hazır:  http://localhost:${PORT}"
echo "▸ Tarayıcıda bu adresi açın. Durdurmak için Ctrl+C."
PORT="$PORT" python -m uvicorn server_fastapi:app --host 0.0.0.0 --port "$PORT"
