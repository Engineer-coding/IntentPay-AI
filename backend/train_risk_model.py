"""
IntentPay AI - Risk Modeli Eğitimi
==================================
Sentetik etiketli veriyle bir XGBoost sınıflandırıcı eğitir ve data/risk_model.json
olarak kaydeder. Eğitim başarısız olursa sistem heuristic fallback kullanır.

Çalıştır:  python backend/train_risk_model.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from data.synthetic import generate_training_data, FEATURE_NAMES

OUT = os.path.join(os.path.dirname(__file__), "data", "risk_model.json")


def main():
    X, y = generate_training_data(6000)
    pos = sum(y)
    print(f"Eğitim örnekleri: {len(y)} | pozitif (riskli): {pos} ({pos/len(y)*100:.1f}%)")

    try:
        import numpy as np
        import xgboost as xgb
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import roc_auc_score, accuracy_score
    except ImportError as e:
        print(f"[uyarı] ML kütüphaneleri yok ({e}); sistem heuristic fallback kullanacak.")
        return

    Xa = np.array(X, dtype=float)
    ya = np.array(y, dtype=int)
    Xtr, Xte, ytr, yte = train_test_split(Xa, ya, test_size=0.2, random_state=42)

    model = xgb.XGBClassifier(
        n_estimators=120, max_depth=4, learning_rate=0.15,
        subsample=0.9, colsample_bytree=0.9,
        eval_metric="logloss", random_state=42,
    )
    model.fit(Xtr, ytr)

    pred = model.predict_proba(Xte)[:, 1]
    auc = roc_auc_score(yte, pred)
    acc = accuracy_score(yte, (pred >= 0.5).astype(int))
    print(f"Test AUC: {auc:.3f} | Accuracy: {acc:.3f}")

    imp = model.feature_importances_
    ranked = sorted(zip(FEATURE_NAMES, imp), key=lambda x: -x[1])
    print("\nÖzellik önemleri (en yüksek 6):")
    for name, v in ranked[:6]:
        print(f"  {name:20s} {v:.3f}")

    model.save_model(OUT)
    print(f"\nModel kaydedildi: {OUT}")


if __name__ == "__main__":
    main()